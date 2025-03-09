import os
import logging
import asyncio
import aiohttp
from bs4 import BeautifulSoup
import telegram
from telegram.error import TimedOut, RetryAfter
import random
import json
import certifi
import ssl

# 设置日志（启用调试模式）
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# 从环境变量中获取配置
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')  # 替换为您的 Token
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')  # 替换为正确的 Chat ID
MAX_RETRIES = int(os.getenv('MAX_RETRIES', 3))
TIMEOUT = int(os.getenv('TIMEOUT', 30))
BASE_URL = "https://my.frantech.ca"
MONITOR_URLS = os.getenv(
    'MONITOR_URLS',
    'https://my.frantech.ca/cart.php?gid=46,https://my.frantech.ca/cart.php?gid=49,https://my.frantech.ca/cart.php?gid=45,https://my.frantech.ca/cart.php?gid=42,https://my.frantech.ca/cart.php?gid=37,https://my.frantech.ca/cart.php?gid=38,https://my.frantech.ca/cart.php?gid=39,https://my.frantech.ca/cart.php?gid=48'
)
PRODUCT_INFO_FILE = "/www/wwwroot/buyvm/product_info.json"
CONFIG_FILE = "/www/wwwroot/buyvm/config.json"

# 翻译字典
translation_dict = {
    'LV': '拉斯维加斯',
    'NY': '纽约',
    'MIA': '迈阿密',
    'LU': '卢森堡',
    'Unmetered BW': '不限流量',
    'mo': '月'
}

SEMAPHORE = asyncio.Semaphore(2)

# 加载配置文件
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                logging.warning(f"{CONFIG_FILE} 为空，使用默认空配置")
                return {}
            try:
                config = json.loads(content).get("products", {})
                logging.info(f"成功加载配置: {config}")
                return config
            except json.JSONDecodeError as e:
                logging.error(f"解析 {CONFIG_FILE} 失败: {e}，使用默认空配置")
                return {}
    logging.error(f"{CONFIG_FILE} 不存在，请创建配置文件")
    return {}

# 保存商品信息
def save_product_info(product_info):
    try:
        with open(PRODUCT_INFO_FILE, 'w', encoding='utf-8') as f:
            json.dump(product_info, f, ensure_ascii=False, indent=4)
        logging.info("商品信息已保存。")
    except Exception as e:
        logging.error(f"保存商品信息时出错: {e}")

# 加载商品信息
def load_product_info():
    if os.path.exists(PRODUCT_INFO_FILE):
        with open(PRODUCT_INFO_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

# 检查消息是否已发送
async def is_message_already_sent(message):
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    try:
        updates = await bot.get_updates(limit=5)
        for update in updates:
            if update.message and update.message.text == message:
                return True
        return False
    except Exception as e:
        logging.error(f"检查消息是否已发送时出错: {e}")
        return False

# 发送消息到 Telegram
async def send_telegram_message(message):
    async with SEMAPHORE:
        bot = telegram.Bot(token=TELEGRAM_TOKEN)
        retries = 0
        while retries < MAX_RETRIES:
            try:
                if await is_message_already_sent(message):
                    logging.info("消息已存在，跳过发送")
                    return None
                sent_message = await bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=message,
                    parse_mode='HTML'
                )
                logging.info(f"消息发送成功: {message}")
                await asyncio.sleep(random.uniform(1, 3))
                return sent_message
            except RetryAfter as e:
                wait_time = e.retry_after
                logging.warning(f"达到速率限制，等待 {wait_time} 秒后重试...")
                await asyncio.sleep(wait_time)
            except TimedOut:
                retries += 1
                logging.warning(f"发送超时，正在重试... {retries}/{MAX_RETRIES}")
                await asyncio.sleep(2)
            except Exception as e:
                logging.error(f"发送消息失败: {e}")
                return None
        return None

# 编辑 Telegram 消息
async def edit_telegram_message(chat_id, message_id, new_message):
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    retries = 0
    while retries < MAX_RETRIES:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=new_message,
                parse_mode='HTML'
            )
            logging.info(f"编辑消息成功: {new_message}")
            await asyncio.sleep(random.uniform(1, 3))
            return True
        except RetryAfter as e:
            wait_time = e.retry_after
            logging.warning(f"达到速率限制，等待 {wait_time} 秒后重试...")
            await asyncio.sleep(wait_time)
        except telegram.error.TimedOut:
            retries += 1
            logging.warning(f"编辑超时，正在重试... {retries}/{MAX_RETRIES}")
            await asyncio.sleep(2)
        except telegram.error.BadRequest as e:
            if "Message is not modified" in str(e):
                logging.info("消息内容未更改，跳过编辑")
                return True
            elif "Message to edit not found" in str(e):
                logging.warning("消息未找到，可能已被删除")
                return False
            else:
                logging.error(f"编辑消息失败: {e}")
                return False
        except Exception as e:
            logging.error(f"编辑消息失败: {e}")
            return False

# 构建商品消息
def build_product_message(name, price, features_str, availability, full_link, remark=""):
    for key, value in translation_dict.items():
        features_str = features_str.replace(key, value)
        name = name.replace(key, value)
        price = price.replace(key, value)
    
    if 'Available' in availability:
        try:
            qty = int(availability.split()[0])
        except Exception:
            qty = 0
        if qty <= 0:
            availability = f"❌ 已售罄：{availability}"
            full_link = f"<s>{full_link}</s>"
        else:
            availability = f"✅ 已补货：{availability}"
    else:
        availability = f"库存未知：{availability}"
    
    remark_text = f"{remark}" if remark else ""
    logging.debug(f"构建消息 - 商品: {name}, remark: {remark}, 消息备注部分: {remark_text}")
    return (
        f"<b>{name}</b> - <b>{price}</b>\n\n"
        f"<blockquote>{features_str}\n{price}</blockquote>\n"
        f"{remark_text}\n\n"
        f"{availability}\n"
        f"链接: {full_link}"
    )

# 库存变化判断
def is_inventory_changed(previous_qty, current_qty, previous_status, current_status):
    return previous_status != current_status

# 采集函数
async def fetch_and_parse_products(url, product_info, config, first_run=False):
    retries = 0
    while retries < MAX_RETRIES:
        try:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=TIMEOUT, ssl=ssl_context) as response:
                    if response.status != 200:
                        logging.warning(f"网页状态码非 200，跳过检测: {url} (状态码: {response.status})")
                        return
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    title_tag = soup.find('title')
                    if title_tag and "FranTech" not in title_tag.get_text(strip=True):
                        logging.warning(f"网页标题不含 'FranTech'，跳过检测: {url}")
                        return
                    break
        except Exception as e:
            retries += 1
            logging.warning(f"请求失败，正在重试... {retries}/{MAX_RETRIES}")
            await asyncio.sleep(2)
            if retries == MAX_RETRIES:
                logging.error(f"请求失败: {e}")
                return

    products = soup.find_all('div', class_='package')
    logging.info(f"【{url}】找到 {len(products)} 个商品")

    for product in products:
        try:
            name_tag = product.find('h3', class_='package-name')
            name = name_tag.get_text(strip=True) if name_tag else "未知商品"
            price = product.find('div', class_='price').get_text(strip=True) if product.find('div', class_='price') else "价格未提供"
            availability = product.find('div', class_='package-qty').get_text(strip=True) if product.find('div', class_='package-qty') else "库存未知"
            link = product.find('a', class_='btn btn-lg btn-primary')['href'] if product.find('a', class_='btn btn-lg btn-primary') else None
            if not link:
                logging.warning(f"商品 {name} 没有购买链接，跳过")
                continue

            # 检查商品是否在 config 中
            if name not in config:
                logging.info(f"商品 {name} 不在 {CONFIG_FILE} 中，跳过")
                continue

            features = [li.get_text(strip=True) for li in product.find('div', class_='package-content').find_all('li') if li.get_text(strip=True)] or \
                       [p.get_text(strip=True) for p in product.find('div', class_='package-content').find_all('p') if p.get_text(strip=True)]
            features_str = '\n'.join(features)
            full_link = f"{BASE_URL}/aff.php?aff=3519&pid={link.split('=')[-1]}"

            qty = int(availability.split()[0]) if 'Available' in availability else 0

            # 每次运行都从 config 获取 notify 和 remark
            product_config = config[name]
            notify = product_config.get('notify')
            remark = product_config.get('remark', "")
            logging.debug(f"商品 {name} - notify: {notify}, remark: {remark}")

            if notify is None:
                logging.warning(f"商品 {name} 的 notify 未定义，跳过")
                continue

            # 更新 product_info 中的库存信息，无论 notify 值
            if name not in product_info:
                product_info[name] = {
                    'qty': qty,
                    'message_id': None,
                    'notify': notify,
                    'remark': remark
                }
            previous_qty = product_info[name].get('qty', 0)
            previous_status = 'in_stock' if previous_qty > 0 else 'out_of_stock'
            current_status = 'in_stock' if qty > 0 else 'out_of_stock'

            # 构建消息
            message = build_product_message(name, price, features_str, availability, full_link, remark)

            # 仅当 notify 为 True 时处理消息
            if notify:
                if is_inventory_changed(previous_qty, qty, previous_status, current_status):
                    if current_status == 'in_stock':
                        sent_message = await send_telegram_message(message)
                        if sent_message:
                            product_info[name] = {
                                'qty': qty,
                                'message_id': sent_message.message_id,
                                'notify': notify,
                                'remark': remark
                            }
                            save_product_info(product_info)
                    else:
                        if name in product_info and product_info[name].get('message_id'):
                            edit_success = await edit_telegram_message(
                                chat_id=TELEGRAM_CHAT_ID,
                                message_id=product_info[name]['message_id'],
                                new_message=message
                            )
                            if edit_success:
                                logging.info(f"库存变为0，消息已编辑: {name}")
                        product_info[name] = {
                            'qty': qty,
                            'message_id': product_info[name].get('message_id'),
                            'notify': notify,
                            'remark': remark
                        }
                        save_product_info(product_info)
                elif current_status == 'in_stock' and qty != previous_qty:
                    if name in product_info and product_info[name].get('message_id'):
                        edit_success = await edit_telegram_message(
                            chat_id=TELEGRAM_CHAT_ID,
                            message_id=product_info[name]['message_id'],
                            new_message=message
                        )
                        if not edit_success:
                            sent_message = await send_telegram_message(message)
                            if sent_message:
                                product_info[name] = {
                                    'qty': qty,
                                    'message_id': sent_message.message_id,
                                    'notify': notify,
                                    'remark': remark
                                }
                                save_product_info(product_info)
                        else:
                            product_info[name]['qty'] = qty
                            save_product_info(product_info)
                    else:
                        sent_message = await send_telegram_message(message)
                        if sent_message:
                            product_info[name] = {
                                'qty': qty,
                                'message_id': sent_message.message_id,
                                'notify': notify,
                                'remark': remark
                            }
                            save_product_info(product_info)
            else:
                logging.info(f"商品 {name} 的 notify 为 False，跳过消息发送")
                product_info[name] = {
                    'qty': qty,
                    'message_id': product_info.get(name, {}).get('message_id'),
                    'notify': notify,
                    'remark': remark
                }
            # 始终保存 product_info 以更新库存状态
            save_product_info(product_info)

        except Exception as e:
            logging.error(f"解析商品 {name} 时出错: {e}")

# 定时任务
async def periodic_task(urls):
    product_info = load_product_info()
    while True:
        config = load_config()  # 每次循环都重新加载 config
        if not config:
            logging.error("配置为空，跳过本次循环")
            await asyncio.sleep(30)
            continue
        tasks = [fetch_and_parse_products(url, product_info, config, first_run=False) for url in urls]
        await asyncio.gather(*tasks)
        await asyncio.sleep(30)

# 主函数
async def main():
    logging.info("启动库存监控任务...")
    urls = MONITOR_URLS.split(',')
    first_run = not os.path.exists(PRODUCT_INFO_FILE)
    config = load_config()
    if not config:
        logging.error("配置为空，程序退出")
        return
    product_info = load_product_info()

    if first_run:
        logging.info("首次运行，初始化商品信息...")
        init_tasks = [fetch_and_parse_products(url, product_info, config, first_run=True) for url in urls]
        await asyncio.gather(*init_tasks)
        save_product_info(product_info)
        logging.info("初始化完成，商品信息已保存。")
    await periodic_task(urls)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logging.info("程序被手动终止")
    finally:
        loop.close()
