#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
商品监控 API 服务（自动定时监控 + 价格对比 + Cookie 可靠检测）
"""

import re
import json
import os
import sqlite3
import requests
from urllib.parse import urlparse, parse_qs
from datetime import datetime
from threading import Lock
from flask import Flask, request, jsonify, render_template
from apscheduler.schedulers.background import BackgroundScheduler

# ======================== 配置 ========================
COOKIE_FILE = "cookies.txt"
DATABASE = "monitor.db"
AUTO_MONITOR_INTERVAL = 30           # 分钟
MONITOR_LOCK = Lock()

PLATFORM_HEADERS = {
    "taobao": {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "accept-language": "zh-CN,zh;q=0.9",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "priority": "u=0, i",
        "sec-ch-ua": "\"Google Chrome\";v=\"147\", \"Not.A/Brand\";v=\"8\", \"Chromium\";v=\"147\"",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Windows\"",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    },
    "tmall": {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "accept-language": "zh-CN,zh;q=0.9",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "priority": "u=0, i",
        "sec-ch-ua": "\"Google Chrome\";v=\"147\", \"Not.A/Brand\";v=\"8\", \"Chromium\";v=\"147\"",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Windows\"",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "referer": "https://www.tmall.com/",
        "origin": "https://detail.tmall.com"
    }
}

app = Flask(__name__)

# ======================== 数据库 ========================
def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS monitor_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            url TEXT NOT NULL,
            platform TEXT,
            item_id TEXT,
            shop_name TEXT,
            title TEXT,
            min_price REAL,
            images TEXT,
            params TEXT
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON monitor_records(timestamp)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_item_id ON monitor_records(item_id)')
    conn.commit()
    conn.close()

def save_record(record):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO monitor_records 
        (timestamp, url, platform, item_id, shop_name, title, min_price, images, params)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        record['timestamp'],
        record['url'],
        record['platform'],
        record['item_id'],
        record['shop_name'],
        record['title'],
        record['min_price'],
        json.dumps(record.get('images', []), ensure_ascii=False),
        json.dumps(record.get('params', {}), ensure_ascii=False)
    ))
    conn.commit()
    conn.close()

def get_all_unique_items():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('SELECT DISTINCT url, item_id FROM monitor_records WHERE url IS NOT NULL AND url != ""')
    items = c.fetchall()
    conn.close()
    return items

def get_last_price(item_id):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('SELECT id, min_price, timestamp FROM monitor_records WHERE item_id = ? ORDER BY timestamp DESC LIMIT 1', (item_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {'id': row[0], 'price': row[1], 'timestamp': row[2]}
    return None

def get_records_with_filters(limit=100, offset=0, keyword=None, start_date=None, end_date=None):
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    query = 'SELECT * FROM monitor_records WHERE 1=1'
    params = []
    if keyword:
        query += ' AND (shop_name LIKE ? OR title LIKE ?)'
        like = f'%{keyword}%'
        params.extend([like, like])
    if start_date:
        query += ' AND timestamp >= ?'
        params.append(start_date)
    if end_date:
        query += ' AND timestamp <= ?'
        params.append(end_date + ' 23:59:59')
    query += ' ORDER BY timestamp DESC LIMIT ? OFFSET ?'
    params.extend([limit, offset])
    c.execute(query, params)
    rows = c.fetchall()
    records = []
    for row in rows:
        rec = dict(row)
        rec['images'] = json.loads(rec['images']) if rec['images'] else []
        rec['params'] = json.loads(rec['params']) if rec['params'] else {}
        last = get_last_price(rec['item_id'])
        if last and last['id'] != rec['id']:
            rec['last_price'] = last['price']
            rec['price_change'] = rec['min_price'] - last['price'] if last['price'] is not None else 0
            rec['price_change_percent'] = (rec['price_change'] / last['price'] * 100) if last['price'] else 0
        else:
            rec['last_price'] = None
            rec['price_change'] = 0
            rec['price_change_percent'] = 0
        records.append(rec)
    count_query = 'SELECT COUNT(*) as total FROM monitor_records WHERE 1=1'
    count_params = []
    if keyword:
        count_query += ' AND (shop_name LIKE ? OR title LIKE ?)'
        count_params.extend([like, like])
    if start_date:
        count_query += ' AND timestamp >= ?'
        count_params.append(start_date)
    if end_date:
        count_query += ' AND timestamp <= ?'
        count_params.append(end_date + ' 23:59:59')
    c.execute(count_query, count_params)
    total = c.fetchone()['total']
    conn.close()
    return records, total

def delete_record_by_id(record_id):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('DELETE FROM monitor_records WHERE id = ?', (record_id,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted > 0

def delete_all_records():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('DELETE FROM monitor_records')
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted

# ======================== Cookie 操作（增强检测）=======================
def load_cookie_from_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            cookie_str = f.read().strip()
        if not cookie_str:
            raise ValueError("Cookie文件为空")
        cookie_dict = {}
        for item in cookie_str.split(';'):
            item = item.strip()
            if not item or '=' not in item:
                continue
            name, value = item.split('=', 1)
            cookie_dict[name] = value
        return cookie_dict
    except Exception as e:
        print(f"[ERROR] 加载Cookie失败: {e}")
        raise

def save_cookie_to_file(cookie_str, file_path):
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(cookie_str.strip())

def check_cookie_valid(cookies):
    """更可靠的检测：访问一个公开商品页，检查是否被重定向到登录页"""
    test_url = "https://item.taobao.com/item.htm?id=1008515679569"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(test_url, headers=headers, cookies=cookies, timeout=10, allow_redirects=False)
        # 302 重定向且 Location 包含 login/passport → 失效
        if resp.status_code == 302:
            location = resp.headers.get('Location', '').lower()
            if 'login' in location or 'passport' in location:
                return False
        # 检查页面内容是否包含登录关键词
        if 'login.taobao.com' in resp.text or 'passport' in resp.text.lower():
            return False
        # 简单判断是否包含商品相关词汇
        if 'item' in resp.text.lower() or 'detail' in resp.text.lower():
            return True
        return False
    except Exception as e:
        print(f"[WARN] Cookie验证异常: {e}")
        return False

# ======================== 监控核心 ========================
def parse_url_and_params(url):
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    query_params = parse_qs(parsed.query)
    params = {k: v[0] for k, v in query_params.items()}
    host = parsed.netloc.lower()
    if "taobao.com" in host:
        platform = "taobao"
    elif "tmall.com" in host:
        platform = "tmall"
    else:
        platform = "unknown"
    return platform, base_url, params

def fetch_page(url, params, headers, cookies):
    resp = requests.get(url, headers=headers, cookies=cookies, params=params, timeout=15)
    resp.raise_for_status()
    return resp

def extract_ice_context(html):
    patterns = [
        r'window\.__ICE_APP_CONTEXT__\s*=\s*(\{[\s\S]*?\});',
        r'var\s+b\s*=\s*(\{[\s\S]*?\});'
    ]
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            json_str = match.group(1).rstrip(';')
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                continue
    return None

def safe_get(data, *keys, default=''):
    temp = data
    for key in keys:
        if isinstance(temp, dict):
            temp = temp.get(key)
            if temp is None:
                return default
        else:
            return default
    return temp if temp is not None else default

def extract_price_from_text(price_str):
    if not price_str:
        return None
    cleaned = re.sub(r'[^0-9.]', '', str(price_str))
    if cleaned:
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None

def parse_sku_min_price(sku2info):
    real_skus = {k: v for k, v in sku2info.items() if k != '0'}
    min_price = None
    for sku_data in real_skus.values():
        price_value = None
        sub_price = sku_data.get('subPrice', {})
        if sub_price:
            price_text = sub_price.get('priceText', '')
            if price_text:
                price_value = extract_price_from_text(price_text)
        if price_value is None:
            price_info = sku_data.get('price', {})
            if price_info:
                price_text = price_info.get('priceText', '')
                if price_text:
                    price_value = extract_price_from_text(price_text)
        if price_value is None:
            direct_price = sku_data.get('price')
            if direct_price is not None:
                price_value = extract_price_from_text(str(direct_price))
        if price_value is None:
            amount = sku_data.get('amount')
            if amount is not None:
                price_value = extract_price_from_text(str(amount))
        if price_value is None:
            promo = sku_data.get('promotionPrice')
            if promo is not None:
                price_value = extract_price_from_text(str(promo))
        if price_value is not None and price_value > 0:
            if min_price is None or price_value < min_price:
                min_price = price_value
    return min_price if min_price is not None else 0

def extract_extension_info(infos):
    result = {'params': {}}
    for item in infos:
        if item.get('type') == 'BASE_PROPS':
            for sub in item.get('items', []):
                param_name = sub.get('title')
                param_values = sub.get('text', [])
                if param_name:
                    if len(param_values) == 1:
                        result['params'][param_name] = param_values[0]
                    else:
                        result['params'][param_name] = param_values
    return result

def monitor_item(url, cookies, auto_save=True):
    platform, base_url, params = parse_url_and_params(url)
    if platform == "unknown":
        return {"success": False, "error": "无法识别平台，仅支持 taobao.com 或 tmall.com"}

    headers = PLATFORM_HEADERS.get(platform, PLATFORM_HEADERS["taobao"])
    try:
        resp = fetch_page(base_url, params, headers, cookies)
    except Exception as e:
        return {"success": False, "error": f"网络请求失败: {str(e)}"}

    data = extract_ice_context(resp.text)
    if not data:
        return {"success": False, "error": "未找到页面数据，可能Cookie失效或页面结构变化", "cookie_expired": True}

    res = safe_get(data, 'loaderData', 'home', 'data', 'res', default={})
    if not res:
        return {"success": False, "error": "未找到商品数据 res", "cookie_expired": True}

    shop_name = safe_get(res, 'seller', 'shopName')
    title = safe_get(res, 'item', 'title')
    item_id = safe_get(res, 'item', 'itemId')
    images = safe_get(res, 'item', 'images', default=[])
    right_bar_price_text = safe_get(res, 'componentsVO', 'priceVO', 'price', 'priceText', default='')

    sku2info = safe_get(res, 'skuCore', 'sku2info', default={})
    if sku2info:
        min_price = parse_sku_min_price(sku2info)
    else:
        min_price = 0

    if min_price == 0 and right_bar_price_text:
        price_match = re.search(r'(\d+(?:\.\d+)?)', right_bar_price_text)
        if price_match:
            min_price = float(price_match.group(1))

    extension_infos = safe_get(res, 'componentsVO', 'extensionInfoVO', 'infos', default=[])
    params_dict = extract_extension_info(extension_infos)['params'] if extension_infos else {}

    last_info = get_last_price(item_id) if item_id else None
    last_price = last_info['price'] if last_info else None

    result = {
        "success": True,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "url": url,
        "platform": platform,
        "item_id": item_id,
        "shop_name": shop_name,
        "title": title,
        "min_price": min_price,
        "images": images[:3],
        "params": params_dict,
        "last_price": last_price,
        "price_change": (min_price - last_price) if last_price is not None else 0,
        "price_change_percent": ((min_price - last_price) / last_price * 100) if last_price and last_price > 0 else 0
    }

    if auto_save and result['success']:
        save_record(result)
    return result

# ======================== 自动监控调度 ========================
def auto_monitor_all():
    print(f"[INFO] 开始自动监控所有商品 - {datetime.now()}")
    try:
        cookies = load_cookie_from_file(COOKIE_FILE)
    except Exception as e:
        print(f"[ERROR] 自动监控加载Cookie失败: {e}")
        return

    items = get_all_unique_items()
    print(f"[INFO] 发现 {len(items)} 个不同商品，开始逐一监控")
    for url, item_id in items:
        with MONITOR_LOCK:
            print(f"[INFO] 自动监控商品: {item_id}")
            result = monitor_item(url, cookies, auto_save=True)
            if result['success']:
                if result['last_price'] is not None:
                    change = result['price_change']
                    if change < 0:
                        print(f"[INFO] 商品 {item_id} 降价了！ {result['last_price']} -> {result['min_price']} (降{abs(change):.2f}元)")
                    elif change > 0:
                        print(f"[INFO] 商品 {item_id} 涨价了！ {result['last_price']} -> {result['min_price']} (涨{change:.2f}元)")
                    else:
                        print(f"[INFO] 商品 {item_id} 价格不变: {result['min_price']}")
                else:
                    print(f"[INFO] 商品 {item_id} 首次监控，价格 {result['min_price']}")
            else:
                print(f"[ERROR] 自动监控失败 {item_id}: {result.get('error', '未知错误')}")
    print("[INFO] 自动监控全部完成")

scheduler = BackgroundScheduler()
scheduler.add_job(func=auto_monitor_all, trigger='interval', minutes=AUTO_MONITOR_INTERVAL, id='auto_monitor')
scheduler.start()

# ======================== API 路由 ========================
@app.route('/api/monitor', methods=['POST'])
def api_monitor():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({"success": False, "error": "缺少 url 参数"}), 400
    url = data['url'].strip()
    if not url:
        return jsonify({"success": False, "error": "url 不能为空"}), 400

    try:
        cookies = load_cookie_from_file(COOKIE_FILE)
    except Exception as e:
        return jsonify({"success": False, "error": f"加载Cookie失败: {str(e)}"}), 500

    result = monitor_item(url, cookies, auto_save=True)
    return jsonify(result)

@app.route('/api/records', methods=['GET'])
def api_records():
    limit = request.args.get('limit', 100, type=int)
    offset = request.args.get('offset', 0, type=int)
    keyword = request.args.get('keyword', '').strip()
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    records, total = get_records_with_filters(limit, offset, keyword if keyword else None, start_date, end_date)
    return jsonify({
        "success": True,
        "records": records,
        "total": total,
        "limit": limit,
        "offset": offset
    })

@app.route('/api/records/<int:record_id>', methods=['DELETE'])
def api_delete_record(record_id):
    if delete_record_by_id(record_id):
        return jsonify({"success": True, "message": f"记录 {record_id} 已删除"})
    else:
        return jsonify({"success": False, "error": "记录不存在"}), 404

@app.route('/api/records', methods=['DELETE'])
def api_delete_all_records():
    if request.args.get('all', '0') == '1':
        count = delete_all_records()
        return jsonify({"success": True, "message": f"已删除 {count} 条记录"})
    else:
        return jsonify({"success": False, "error": "请使用 ?all=1 确认删除所有记录"}), 400

@app.route('/api/cookie', methods=['POST'])
def api_update_cookie():
    data = request.get_json()
    if not data or 'cookie' not in data:
        return jsonify({"success": False, "error": "缺少 cookie 参数"}), 400
    cookie_str = data['cookie'].strip()
    if not cookie_str:
        return jsonify({"success": False, "error": "cookie 不能为空"}), 400

    try:
        save_cookie_to_file(cookie_str, COOKIE_FILE)
        return jsonify({"success": True, "message": "Cookie 已更新"})
    except Exception as e:
        return jsonify({"success": False, "error": f"写入失败: {str(e)}"}), 500

@app.route('/api/cookie/status', methods=['GET'])
def api_cookie_status():
    try:
        cookies = load_cookie_from_file(COOKIE_FILE)
        valid = check_cookie_valid(cookies)
        return jsonify({"success": True, "valid": valid})
    except Exception as e:
        return jsonify({"success": False, "valid": False, "error": str(e)})

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    init_db()
    try:
        load_cookie_from_file(COOKIE_FILE)
        print("[INFO] Cookie 加载成功，服务启动")
    except Exception as e:
        print(f"[WARN] Cookie 加载失败: {e}，请确保 {COOKIE_FILE} 文件存在且格式正确")
    print(f"[INFO] 自动监控间隔: {AUTO_MONITOR_INTERVAL} 分钟")
    app.run(host='0.0.0.0', port=5000, debug=False)