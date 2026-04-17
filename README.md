# 淘宝/天猫商品监控系统

基于 Flask + SQLite + Chart.js 的商品价格监控看板，支持：
- 手动/自动定时监控（每30分钟）
- SKU最低价提取 + 价格回退机制
- 价格变化对比（与上次记录对比）
- 历史记录查询、搜索、导出CSV
- 最近7天价格趋势图
- Cookie 热更新

## 快速开始
1. 安装依赖：`pip install -r requirements.txt`
2. 准备 cookies.txt（浏览器导出 Header String 格式）
3. 运行 `python app.py`
4. 访问 `http://localhost:5000`

## 截图
<img width="1349" height="778" alt="image" src="https://github.com/user-attachments/assets/93aee6ad-92bd-4412-b6ba-acedf4beb2cd" />
<img width="1337" height="336" alt="image" src="https://github.com/user-attachments/assets/b95c1bcf-4e10-4604-bbad-e00c81f64756" />


## 技术栈
- 后端：Flask, APScheduler, requests
- 前端：Chart.js, 原生 JS
- 数据库：SQLite

## License
MIT
