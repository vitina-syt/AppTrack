# Windows Server 服务部署指南（NSSM）

## 前置条件

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows Server 2019 或更高 |
| NSSM | 已安装至 `C:\tools\nssm`，且已加入系统 PATH |
| Python | 已在 `backend\venv` 创建虚拟环境并安装依赖 |
| 前端 | `frontend\dist\` 目录已构建 |

---

## 一、部署前准备

### 1. 安装 Python 依赖

```powershell
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements-server.txt
```

### 2. 构建前端

```powershell
cd frontend
npm install
npm run build
```

### 3. 配置 API Key

在 `backend\` 目录下创建 `.env` 文件：

```
ANTHROPIC_API_KEY=sk-ant-xxxxxxxx
```

---

## 二、安装服务

使用**管理员**身份打开 PowerShell，进入项目根目录后执行：

```powershell
.\scripts\install-windows-service.ps1
```

安装成功后终端会显示：

```
Done. StepCast is running at http://localhost:8001
Gallery : http://localhost:8001/gallery
API docs: http://localhost:8001/docs
```

---

## 三、卸载服务

```powershell
.\scripts\uninstall-windows-service.ps1
```

---

## 四、常用管理命令

```powershell
# 查看服务状态
nssm status StepCast

# 启动 / 停止 / 重启
nssm start   StepCast
nssm stop    StepCast
nssm restart StepCast

# 查看实时日志
Get-Content logs\StepCast.log -Wait -Tail 50

# 查看错误日志
Get-Content logs\StepCast-error.log -Tail 100
```

也可以在「服务」管理器（`services.msc`）中找到 **StepCast Server** 进行管理。

---

## 五、代码更新后重新部署

```powershell
# 1. 拉取最新代码（或重新上传）

# 2. 如果 Python 依赖有变化
cd backend
venv\Scripts\activate
pip install -r requirements-server.txt

# 3. 如果前端有变化
cd frontend
npm run build

# 4. 重启服务（管理员 PowerShell）
nssm restart StepCast
```

---

## 六、常见问题

### 服务启动后立即停止

查看错误日志定位原因：

```powershell
Get-Content logs\StepCast-error.log -Tail 50
```

常见原因：
- `.env` 文件不存在或 API Key 填写有误
- `frontend\dist\` 目录不存在（需先构建前端）
- Python 依赖未安装完整

### 端口 8001 被占用

```powershell
netstat -ano | findstr :8001
```

找到占用的 PID 后结束进程，或修改 `install-windows-service.ps1` 中的端口号（`--port 8001`）。

### 重新安装服务

直接重新执行安装脚本即可，脚本会自动先卸载旧服务再重新安装：

```powershell
.\scripts\install-windows-service.ps1
```
