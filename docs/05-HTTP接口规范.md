# 教务系统 HTTP API v1

本规范供后端实现、前端类型生成及联调使用。业务数据保存在本项目的 MySQL 表中。接口沿用学生、任课教师、管理员三个角色。正式路由统一使用 `/api/v1` 前缀。下表路径均省略该前缀。

## 1. 传输、响应和会话

- 输入与输出为 UTF-8 JSON。字段使用 `snake_case`。学号、工号、课程码和学期码始终是字符串。记录主键在 v1 为整数。学分、分数、权重和绩点按十进制传输，响应使用字符串，例如 `"87.00"`。
- 日期用 `YYYY-MM-DD`。时间窗口提交带偏移量的 ISO 8601 值，例如 `2026-11-23T09:00:00+08:00`，服务器写库时换成 UTC。选课和成绩操作使用服务器当前时间。时间窗口包含开始时刻，排除结束时刻。
- 成功响应形如 `{"data":{"section_id":17}}` 或 `{"data":[...]}`。搜索分页响应在 `data` 中返回 `items`、`limit`、`offset`，待办使用 `limit` 和两个游标字段 `after_requested_at`、`after_request_id` 按固定顺序翻页。时间戳相同按主键排序。
- 登录成功写入 `course_session` HttpOnly Cookie，默认 Secure，SameSite=Strict，有效期 8 小时。会话标识在数据库中以摘要保存。登录和 `GET /auth/me` 都返回 `csrf_token`。已登录的写请求在 `X-CSRF-Token` 中携带该值。退出、改密和锁定后被撤销的会话返回 401。
- 登录返回角色列表，前端可据此显示菜单。资源访问权限和本人、本班数据范围仍由服务端执行。写接口只从 Cookie 确认执行人，管理员为他人办理时才传入业务对象的 ID。
- `GET /openapi.json` 是可机器读取的 OpenAPI。静态快照保存在 [05-openapi.json](05-openapi.json)。`GET /docs` 提供交互式调试。开发环境前端可通过配置交换 Cookie，示例见[前端联调](#4-前端联调)。

## 2. 接口清单

**账户和基础数据**

| 方法 | 路径 | 权限 | 主要输入 / 输出 |
|---|---|---|---|
| GET | `/health` | 公开 | 数据库连通状态 |
| POST | `/auth/login` | 公开 | `login_name,password` → 账户摘要、角色和 CSRF 值 |
| GET | `/auth/me` | 登录 | 当前账户和 CSRF 值 |
| DELETE | `/auth/session` | 登录 | 退出当前会话 |
| PATCH | `/auth/password` | 登录 | `old_password,new_password`，改密后重新登录 |
| GET / POST | `/admin/accounts` | 管理员 | 分页查询或创建账户，创建时指定初始角色及关联学号、工号 |
| PATCH | `/admin/accounts/{account_id}/state` | 管理员 | `ACTIVE/LOCKED/DISABLED` |
| PUT | `/admin/accounts/{account_id}/roles/{role}` | 管理员 | `enabled` 控制角色启用与撤回 |
| GET | `/catalog/terms` | 登录 | 学期、起始日期、周数、当前学期标志 |
| GET | `/catalog/courses` | 登录 | `search,limit,offset` → 课程目录 |
| POST | `/admin/catalog` | 管理员 | 按依赖关系批量写入学院、专业、行政班、学期、校区、节次、教师、教室、课程 |
| GET | `/admin/catalog/{kind}` | 管理员 | 按基础数据种类分页读取，供表单选项使用 |
| GET / PUT | `/admin/programs` | 管理员 | 按专业、年级查询，或提交总学分和课程列表 |
| GET | `/me/program` | 学生 | 本人培养方案、计划课程和已修学分 |

**学籍和申请**

| 方法 | 路径 | 权限 | 主要输入 / 输出 |
|---|---|---|---|
| POST | `/admin/student-imports` | 管理员 | `batch_no,students`；重复内容返回原批次 |
| GET | `/admin/students` | 管理员 | 按行政班分页，返回学号、姓名、行政班、学籍状态 |
| GET | `/me/profile` | 学生 | 本人学籍卡片、联系方式和学籍异动摘要 |
| POST | `/me/profile-requests` | 学生 | `changes` 支持电话、邮箱、地址、姓名拼音，生成待审申请 |
| GET | `/me/requests` | 登录 | 本人申请及状态，支持 `limit,offset` |
| GET | `/admin/requests` | 管理员 | 申请状态、可选类型和游标，输出可审核的申请摘要 |
| GET | `/admin/requests/{request_id}` | 管理员 | 单条申请、申请类型对应的审核信息及历史 |
| POST | `/admin/requests/{request_id}/decision` | 管理员 | `decision` 为 `APPROVE/REJECT`。支持资料变更、缓考、成绩更正 |

**课程、排课和选课**

| 方法 | 路径 | 权限 | 主要输入 / 输出 |
|---|---|---|---|
| GET / POST | `/admin/sections` | 管理员 | 按学期列出教学班或从培养方案生成教学班并指定主讲 |
| POST | `/admin/sections/{section_id}/meetings` | 管理员 | 教室、星期、起止节次、周次列表。冲突时返回具体错误码 |
| GET / POST | `/admin/selection-rounds` | 管理员 | 按学期分页，或提交模式、时间、教学班、适用班级和可选学分上限 |
| POST | `/admin/selection-rounds/{round_id}/settle` | 管理员 | 抽签轮次封轮返回中签、落选数量，已封轮可重查 |
| POST | `/admin/selection-rounds/{round_id}/adjustments` | 管理员 | 学号、教学班、选或退与理由 |
| GET | `/selection/rounds` | 学生 | 选课轮次及是否处于选课时间 |
| GET | `/selection/rounds/{round_id}/sections` | 学生 | 适用的教学班、容量、占用和本人的状态 |
| PUT | `/selection/rounds/{round_id}/sections/{section_id}/enrollment` | 学生 | `action` 为 `SELECT/DROP`；返回选课记录与状态 |
| GET | `/me/selections` | 学生 | 学期 → 本人有效选课 |
| GET | `/me/timetable` | 学生、教师 | 学期、周次 → 本人课表 |
| GET | `/teacher/sections` | 教师 | 按学期列出本人教学班 |
| GET | `/teacher/sections/{section_id}/roster` | 教师 | 本班选课名单，含选课记录 ID 用于录成绩 |

**考试、成绩和评教**

| 方法 | 路径 | 权限 | 主要输入 / 输出 |
|---|---|---|---|
| GET / POST | `/admin/exams` | 管理员 | 按学期分页，或提交班、学期、考试类别、时间、考场及监考以发布场次 |
| GET | `/me/exams` | 学生 | 按学期查询本人场次和座位 |
| GET | `/teacher/invigilation` | 教师 | 按学期查询监考安排 |
| POST | `/me/deferral-requests` | 学生 | 本人考场座位记录、理由和证明材料引用 |
| POST | `/admin/grade-scales` | 管理员 | 新增并发布记分制版本。支持百分制区间与等级制 |
| GET | `/grade-scales` | 教师、管理员 | 查询可用于登记单的记分制版本 |
| GET / POST | `/admin/grade-sheets` | 管理员 | 按状态、学期分页或开成绩登记单 |
| GET | `/admin/grade-sheets/{sheet_id}` | 管理员 | 登记单、构成、名单与分数，供审核 |
| GET | `/teacher/grade-sheets` | 教师 | 本人登记单列表 |
| GET | `/teacher/grade-sheets/{sheet_id}` | 教师 | 本班登记单、构成、名单与已暂存分数 |
| PUT | `/teacher/grade-sheets/{sheet_id}/components` | 教师 | 记分制及各分项目权重；已有分数后冻结 |
| PUT | `/teacher/grade-sheets/{sheet_id}/entries` | 教师 | 分数批量暂存，入参为选课记录 ID 和具体分数或等级 |
| POST | `/teacher/grade-sheets/{sheet_id}/submit` | 教师 | 提交齐全的登记单 |
| POST | `/admin/grade-sheets/{sheet_id}/review` | 管理员 | `PUBLISH/RETURN` 和审核意见 |
| GET | `/me/grades` | 学生 | `term,mode=all/best`；按开课学期查询本人已发布成绩 |
| POST | `/teacher/grades/{grade_id}/correction-requests` | 教师 | 本班已发布百分制成绩、新分项和更正原因 |
| POST | `/admin/evaluation-batches` | 管理员 | 学期、时间、名称、类别和指标，生成待评任务 |
| GET | `/evaluation-batches` | 教师、管理员 | 按学期分页，教师只查询与本人有关的批次 |
| POST | `/admin/evaluation-batches/{batch_id}/finish` | 管理员 | 结束后生成评教汇总 |
| GET | `/me/evaluations` | 学生 | 按学期列出本人待评和已评价任务及指标 |
| POST | `/me/evaluations/{assignment_id}/submission` | 学生 | 指标分数与可选评语。每个任务只能提交一次 |
| GET | `/teacher/evaluation-results` | 教师 | `batch_id` → 本人已汇总结果 |
| GET | `/admin/evaluation-results` | 管理员 | `batch_id` → 已汇总结果与参与人数 |

CSV/Excel 文件页面可按上述批量接口提交解析后的数据。毕设、教室外部同步系统、重修缴费、调停课审批及家庭成员维护属于后续接口。v1 使用现有表定义实现表内业务，证明材料只接收引用 ID。

## 3. 错误和重试

统一错误响应形如 `{"error":{"code":"FULL","message":"教学班已满","fields":[]}}`。客户端按 `code` 分支展示，不依赖展示文字。表单错误的 `fields` 中包含 `path,issue`。

| HTTP 状态 | 常见代码 | 处理 |
|---|---|---|
| 400 | `INVALID_CURSOR` | 修正成对的游标参数 |
| 401 | `UNAUTHENTICATED`、`INVALID_CREDENTIALS` | 清理本地界面状态并重新登录 |
| 403 | `FORBIDDEN`、`INVALID_CSRF`、`ORIGIN_DENIED` | 核对角色、资源归属及请求来源 |
| 404 | `NOT_FOUND`、`NO_SECTION`、`NO_GRADE_SHEET` | 刷新所在列表 |
| 409 | `FULL`、`DUPLICATE_COURSE`、`TIMETABLE_CONFLICT`、`ROUND_CLOSED`、`STALE_GRADE_VERSION`、`DUPLICATE_RECORD` | 显示原因，必要时重新读取状态 |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | 登录请求需使用 `application/json` |
| 422 | `VALIDATION_ERROR`、`REFERENCE_INVALID`、`CONSTRAINT_VIOLATION` | 标记相关字段供用户修改 |
| 429 | `LOGIN_RATE_LIMITED` | 按 `Retry-After` 等待 |
| 503 | `DATABASE_UNAVAILABLE`、`TRANSACTION_RETRY` | 读取最新状态后再决定重试写请求 |

选课和退课是目标状态操作，相同请求重复发送不二次增减名额。新生导入通过批次和内容去重。撤回或失败申请可保持在错误后的旧状态。批量登分遇到任何无效记录均回滚。管理员审核重复请求返回当前已办理结果。其他创建类请求遇到网络中断时，需要先查已有资源，再决定重试。

## 4. 前端联调

开发环境使用相同主机名访问前后端，例如前端 `http://127.0.0.1:5173`，后端 `http://127.0.0.1:8000`。在后端配置 `COURSE_FRONTEND_ORIGINS=http://127.0.0.1:5173`。本机 HTTP 联调使用 `COURSE_COOKIE_SECURE=0`。部署时使用 HTTPS 并保留默认 Secure Cookie。Fetch 启用 `credentials: "include"`。同一站点下分开部署前后端时也应在允许来源中明确填写前端完整 Origin。

```js
const base = "http://127.0.0.1:8000/api/v1"
const login = await fetch(`${base}/auth/login`, {
  method: "POST",
  credentials: "include",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ login_name, password })
})
const session = await login.json()
if (!login.ok) throw new Error(session.error.code)

const result = await fetch(`${base}/selection/rounds/1/sections/17/enrollment`, {
  method: "PUT",
  credentials: "include",
  headers: {
    "Content-Type": "application/json",
    "X-CSRF-Token": session.data.csrf_token
  },
  body: JSON.stringify({ action: "SELECT" })
})
const payload = await result.json()
if (!result.ok) throw new Error(payload.error.message)
```

浏览器刷新后通过 `GET /auth/me` 恢复角色和 CSRF 值。按 OpenAPI 的请求和响应模型生成类型。界面按权限显示入口，仍应处理 401、403、409 与表单校验错误。
