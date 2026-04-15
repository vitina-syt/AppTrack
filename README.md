# AppTrack

Windows 桌面软件使用时长追踪工具。自动记录前台应用的切换与停留时长，数据完全本地存储，无需联网。

---

## 功能概览

| 功能 | 说明 |
|------|------|
| 自动追踪 | 每隔 N 秒轮询 Win32 前台窗口，自动记录会话 |
| 实时监控 | 首页实时显示当前活动应用与今日累计时长 |
| 仪表盘 | 今日各应用用量横条排行，自动刷新 |
| 历史记录 | 按日期浏览历史会话与应用用量分布 |
| 本地优先 | 数据存储在本机 SQLite，无需云服务 |
| 多语言 | 中文 / English / Deutsch |
| 双主题 | Tokyo Night 深色 / Siemens Energy 浅色 |

---

## 快速开始

### 环境要求

- Python **3.12+**
- Node.js **18+**

### 1. 启动后端

```bash
cd backend

# 创建虚拟环境（推荐）
py -3.12 -m venv venv
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 启动（默认端口 8001）
uvicorn app.main:app --port 8001 --reload
```

后端启动时会自动：
- 初始化 SQLite 数据库（`backend/data/apptrack.db`）
- 以 5 秒间隔启动后台追踪线程

> **Demo 模式**：未安装 `pywin32` 时追踪器自动模拟 AutoCAD / Outlook / Chrome 等应用切换，便于在非 Windows 环境开发调试。

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev
```

浏览器访问 [http://localhost:5174](http://localhost:5174)

> 前端通过 Vite proxy 将 `/api/*` 转发至 `http://localhost:8001`，无需手动配置跨域。

---

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                      Frontend (React)                   │
│                   http://localhost:5174                 │
│                                                         │
│  ┌──────────┐  ┌────────────┐  ┌──────────┐  ┌──────┐  │
│  │ HomePage │  │ Dashboard  │  │ History  │  │ Set- │  │
│  │ 实时监控  │  │  今日用量   │  │  历史    │  │ tings│  │
│  └──────────┘  └────────────┘  └──────────┘  └──────┘  │
│       │               │               │           │     │
│       └───────────────┴───────────────┴───────────┘     │
│                       Axios /api/*                      │
└───────────────────────────┬─────────────────────────────┘
                            │ HTTP (Vite proxy)
┌───────────────────────────▼─────────────────────────────┐
│                    Backend (FastAPI)                     │
│                   http://localhost:8001                  │
│                                                         │
│  ┌──────────────────┐   ┌──────────────────────────┐    │
│  │   REST API 层    │   │    AppTracker 线程        │    │
│  │                  │   │                          │    │
│  │ /api/tracker/*   │   │  每 N 秒轮询              │    │
│  │ /api/sessions    │◄──│  GetForegroundWindow()   │    │
│  │ /api/stats/*     │   │  → 记录会话到 SQLite      │    │
│  └────────┬─────────┘   └──────────────────────────┘    │
│           │                                             │
│  ┌────────▼─────────────────────────────────────────┐   │
│  │              SQLite  (WAL 模式)                   │   │
│  │           backend/data/apptrack.db               │   │
│  │                                                  │   │
│  │   sessions (id, app_name, exe_path,              │   │
│  │             window_title, started_at,            │   │
│  │             ended_at, duration_seconds)          │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### 追踪器工作流程

```
后端启动
    │
    ▼
init_db()  ──── 建表（sessions）
    │
    ▼
tracker.start(poll_interval=5)
    │
    ▼
┌─────────────── 后台线程循环 ───────────────┐
│                                          │
│  GetForegroundWindow()                   │
│       │                                  │
│       ▼                                  │
│  app_name 与上次相同？                    │
│       │ 否                               │
│       ▼                                  │
│  UPDATE sessions SET ended_at = now      │  ← 关闭旧会话
│  INSERT INTO sessions (app, started_at)  │  ← 开启新会话
│       │                                  │
│  sleep(poll_interval)                    │
│       │                                  │
└───────┘                                  │
    │（收到 stop 信号）                     │
    ▼                                      │
UPDATE sessions SET ended_at = now         ← 优雅关闭当前会话
```

---

## API 接口

### 追踪器控制

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/tracker/status` | 当前状态（是否运行、当前应用） |
| `POST` | `/api/tracker/start?poll_interval=5` | 启动追踪（间隔 1–60 秒） |
| `POST` | `/api/tracker/stop` | 停止追踪 |

**`GET /api/tracker/status` 响应示例：**
```json
{
  "running": true,
  "current_app": "AutoCAD.exe",
  "current_exe": "C:\\Program Files\\Autodesk\\AutoCAD\\AutoCAD.exe",
  "current_title": "Drawing1.dwg — AutoCAD 2025",
  "poll_interval": 5
}
```

### 会话记录

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/sessions?date=YYYY-MM-DD` | 指定日期的会话列表 |
| `GET` | `/api/sessions?limit=200` | 最近 N 条会话 |

### 统计数据

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/stats/today` | 今日各应用用量 |
| `GET` | `/api/stats/date/2025-01-15` | 指定日期用量 |
| `GET` | `/api/stats/days?limit=30` | 近 30 天有记录的日期列表 |

**`GET /api/stats/today` 响应示例：**
```json
[
  { "app_name": "AutoCAD.exe", "exe_path": "C:\\...", "total_seconds": 7200, "session_count": 3 },
  { "app_name": "OUTLOOK.EXE", "exe_path": "C:\\...", "total_seconds": 1800, "session_count": 12 }
]
```

完整交互文档：[http://localhost:8001/docs](http://localhost:8001/docs)

---

## 前端页面

### 监控页（`/`）
- 实时显示追踪状态（绿点 = 运行中）
- 显示当前前台应用名称与窗口标题
- 今日累计时长 + 最常用应用快览
- 一键开始 / 停止追踪

### 仪表盘（`/dashboard`）
- 今日合计时长、应用数、会话数
- 应用使用排行（带颜色横条图，纯 SVG）
- 每 10 秒自动刷新

### 历史（`/history`）
- 左栏：有记录日期列表（含当日总时长）
- 右栏：选定日期的应用用量分布 + 完整会话时间线

### 设置（`/settings`）
- **采样间隔**：1–60 秒，调整后下次 `start` 生效
- **忽略应用**：填写进程名（如 `explorer.exe`），仅过滤前端显示，不影响原始数据
- **主题 / 语言**：实时切换，持久化到 localStorage

---

## 目录结构

```
AppTrack/
├── backend/
│   ├── .python-version          # 3.12
│   ├── requirements.txt
│   └── app/
│       ├── main.py              # FastAPI 入口 + lifespan（启停追踪器）
│       ├── database.py          # SQLite 初始化，线程安全连接
│       ├── tracker.py           # Win32 轮询线程（AppTracker 单例）
│       ├── models.py            # Pydantic 响应模型
│       └── api/routes/
│           ├── tracker_routes.py
│           ├── sessions.py
│           └── stats.py
├── frontend/
│   ├── package.json
│   ├── vite.config.js           # 代理 /api → :8001
│   ├── index.html
│   └── src/
│       ├── index.css            # CSS 变量主题系统
│       ├── main.jsx
│       ├── App.jsx
│       ├── api/index.js         # Axios 封装
│       ├── i18n/locale.js       # 中/英/德
│       ├── store/settingsStore.js  # Zustand + localStorage
│       ├── hooks/useT.js
│       ├── components/Layout/AppLayout.jsx
│       └── pages/
│           ├── HomePage.jsx
│           ├── DashboardPage.jsx
│           ├── HistoryPage.jsx
│           └── SettingsPage.jsx
└── README.md
```

---

## 技术栈

| 层 | 技术 |
|----|------|
| 前端框架 | React 18 + Vite |
| 前端路由 | React Router v6 |
| 状态管理 | Zustand（持久化到 localStorage） |
| HTTP 客户端 | Axios |
| 后端框架 | FastAPI |
| ASGI 服务器 | Uvicorn |
| 数据验证 | Pydantic v2 |
| 数据库 | SQLite（WAL 模式，stdlib `sqlite3`） |
| Windows API | pywin32（`win32gui` / `win32process`） |
| 进程信息 | psutil |
| 样式方案 | CSS 变量 + React inline styles（无 CSS 框架） |

---

## 数据存储

数据库文件：`backend/data/apptrack.db`（SQLite，WAL 模式）

```sql
CREATE TABLE sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name         TEXT    NOT NULL,       -- 进程名，如 AutoCAD.exe
    exe_path         TEXT,                   -- 完整可执行文件路径
    window_title     TEXT,                   -- 窗口标题
    started_at       TEXT    NOT NULL,       -- ISO-8601 UTC
    ended_at         TEXT,                   -- NULL 表示当前活跃会话
    duration_seconds INTEGER DEFAULT 0
);
```

> 当前活跃会话（`ended_at IS NULL`）的时长在查询时实时计算：
> `CAST((julianday('now') - julianday(started_at)) * 86400 AS INTEGER)`

---

## 与 AutoScribe 的关系

AppTrack 与 AutoScribe 共享相同的设计理念：

- **本地优先**：数据存储在本机，不依赖任何云服务
- **技术栈一致**：Python FastAPI 后端 + React 前端
- **设计系统一致**：Tokyo Night 深色主题 / Siemens Energy 浅色主题，相同 CSS 变量体系
- **多语言一致**：相同的 `useT()` hook + `LOCALES` 结构，支持中/英/德
- **状态管理一致**：Zustand + localStorage 持久化模式

两者的区别在于面向的数据来源：AutoScribe 追踪**浏览器操作**（通过 Chrome 扩展），AppTrack 追踪 **Windows 桌面应用**（通过 Win32 API）。
