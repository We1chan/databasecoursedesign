# 教务系统后端

后端使用 FastAPI、Python 3.11+ 和 MySQL 8.0.19+。前端对接以 [HTTP 接口规范](../docs/05-HTTP接口规范.md)及 [OpenAPI 快照](../docs/05-openapi.json)为准。核心业务与课程阶段的 MySQL 事务回归使用同一组实现。

## 本地运行

在仓库根目录为后端建立虚拟环境。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

在 MySQL 中创建一个空数据库，或选用已按第二阶段建表脚本初始化的课程数据库。初始化账号需要能够在**该数据库**内建表、创建索引和写入数据。

```sql
CREATE DATABASE course_design CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
```

配置本次会话中的连接参数和资料加密密钥。

```powershell
$env:COURSE_DB_NAME = 'course_design'
$env:COURSE_DB_HOST = '127.0.0.1'
$env:COURSE_DB_PORT = '3306'
$dbCredential = Get-Credential -Message '课程数据库'
$env:COURSE_DB_USER = $dbCredential.UserName
$env:COURSE_DB_PASSWORD = $dbCredential.GetNetworkCredential().Password
$env:COURSE_DATA_KEY = .\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

上面的密钥生成命令仅在首次初始化时使用。已有加密资料时，重启需要注入同一数据密钥。初始化会按顺序应用基础表结构、待办索引和会话表，并配置三类角色与初始管理员。已存在的管理员密码由其本人管理。

```powershell
.\.venv\Scripts\python.exe -m backend.bootstrap --admin administrator
$env:COURSE_COOKIE_SECURE = '0'
$env:COURSE_FRONTEND_ORIGINS = 'http://127.0.0.1:5173'
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

本地健康检查位于 `http://127.0.0.1:8000/api/v1/health`。Swagger 位于 `/docs`。供前端生成类型的接口描述可从 `/openapi.json` 下载。更新快照运行 `python -m backend.openapi`。

本地使用 HTTP 时仅关闭本地 Cookie 的 Secure 属性。HTTPS 部署保留默认 Secure 属性，前端允许来源按部署地址配置。会话 Cookie 为 HttpOnly、SameSite=Strict，前端通过 `credentials: "include"` 携带。写请求使用登录或 `/auth/me` 返回的 CSRF 值。

## 数据准备和演示顺序

管理员先写入学院、专业、行政班、学期、校区、节次、教师、教室和课程。批量接口按外键依赖顺序写入并整体回滚。随后配置培养方案、导入学生、创建用户和教师账号。管理员可通过各基础数据列表向前端提供选择项。

选课按开课、排课、开放轮次、本人选退课的顺序执行。排考后创建成绩登记单，使用管理员发布的记分制版本，教师设置分项、登分并提交，管理员审核发布。学生读取本人已发布成绩。评教批次按当时的选课名单生成任务，截止后可汇总。

## 验证

安装测试依赖后，将 `DB_TEST_HOST`、`DB_TEST_PORT`、`DB_TEST_USER`、`DB_TEST_PASSWORD` 指向独立 MySQL 测试实例。测试脚本使用该账号创建并清理随机命名的临时数据库。

```powershell
.\.venv\Scripts\python.exe -m pip install -r tests/requirements.txt
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

HTTP 集成测试覆盖会话与跨域、学籍审批和从基础数据到评教的主流程。已有业务回归同时覆盖选课并发、抽签、考试冲突、成绩版本审计及数据库约束。部署到校方真实环境需匹配正式身份、系统数据和访问策略。
