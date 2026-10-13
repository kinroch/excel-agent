---- Excel对话式数据分析智能体 · 建表脚本 V1.0.2 ----
---- 来源：《数据库设计文档 V1.0.2》第 5 章「全量建表 DDL」（唯一权威来源，禁止擅自改动字段与索引）----
---- 落地位置：backend/db/schema.sql，由 app/infra/db.py 的 init_database() 与 scripts/init_db.py 执行 ----
---- 方言：SQLite 3（WAL）；全部使用 IF NOT EXISTS，保证幂等可重复执行（M0 验收 AC）----

-- 数据库运行模式初始化（每次建库/连接时执行）
PRAGMA journal_mode = WAL;       -- WAL 模式：单写多读并发（ADR-001）
PRAGMA foreign_keys = ON;        -- 强制外键约束
PRAGMA synchronous = NORMAL;     -- WAL 下推荐的落盘策略

-- ============================================================
-- Excel对话式数据分析智能体 · 建表脚本 V1.0.2
-- 方言：SQLite 3（WAL）；幂等可重复执行
-- ============================================================

-- 1. users 用户表（PRD 4.1 / 4.9）
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT    NOT NULL,                               -- 账号（手机号/邮箱）
  password_hash TEXT    NOT NULL,                              -- bcrypt 哈希（NFR-S2）
  role          TEXT    NOT NULL DEFAULT 'user'
                CHECK (role IN ('user','admin')),
  status        TEXT    NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','disabled')),
  created_at    TEXT    NOT NULL,
  updated_at    TEXT    NOT NULL,
  last_login_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_users_username
  ON users(username);
CREATE INDEX IF NOT EXISTS ix_users_status_created
  ON users(status, created_at);

-- 2. files 上传文件表（PRD 4.2）
CREATE TABLE IF NOT EXISTS files (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id        INTEGER NOT NULL REFERENCES users(id),
  original_name  TEXT    NOT NULL,
  storage_path   TEXT    NOT NULL,          -- uploads/{user_id}/{file_id}_{name}
  format         TEXT    NOT NULL,          -- xlsx / csv
  size_bytes     INTEGER NOT NULL,          -- ≤50MB（F-4.2-2）
  parse_status   TEXT    NOT NULL DEFAULT 'pending'
                 CHECK (parse_status IN ('pending','parsing','parsed','failed')),
  parse_progress INTEGER NOT NULL DEFAULT 0
                 CHECK (parse_progress BETWEEN 0 AND 100),  -- 0~100
  parse_error    TEXT,
  missing_policy TEXT    NOT NULL DEFAULT 'prompt'
                 CHECK (missing_policy IN ('prompt','fill','drop')),
  quality_json   TEXT,
  created_at     TEXT    NOT NULL,
  deleted_at     TEXT                           -- 软删除标记
);
CREATE INDEX IF NOT EXISTS ix_files_user
  ON files(user_id, deleted_at, created_at);

-- 3. data_sheets Sheet 结构与字段说明表（F-4.2-3/4/8）
CREATE TABLE IF NOT EXISTS data_sheets (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id     INTEGER NOT NULL REFERENCES files(id),
  name        TEXT    NOT NULL,                -- Sheet 名
  row_count   INTEGER NOT NULL,
  col_count   INTEGER NOT NULL,
  header_row  INTEGER NOT NULL DEFAULT 1,
  fields_json TEXT    NOT NULL                 -- 字段说明数组 JSON
);
CREATE INDEX IF NOT EXISTS ix_sheets_file
  ON data_sheets(file_id);

-- 4. sessions 会话表（PRD 4.6）
CREATE TABLE IF NOT EXISTS sessions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL REFERENCES users(id),
  title      TEXT    NOT NULL,                 -- 默认取首问前 20 字
  file_id    INTEGER REFERENCES files(id),     -- 可空：未绑定文件
  created_at TEXT    NOT NULL,
  updated_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_user
  ON sessions(user_id, updated_at);
CREATE INDEX IF NOT EXISTS ix_sessions_updated
  ON sessions(updated_at);

-- 5. messages 消息表（PRD 4.6 / 6.1）
CREATE TABLE IF NOT EXISTS messages (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  role       TEXT    NOT NULL CHECK (role IN ('user','assistant','system')),
  msg_type   TEXT    NOT NULL DEFAULT 'text',  -- text/intent/plan/clarify/chart/report/notice/block
  content    TEXT    NOT NULL,                 -- 文本或结构化 JSON
  task_id    INTEGER REFERENCES analysis_tasks(id),  -- 循环外键，运行时检查
  created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_messages_session
  ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS ix_messages_task
  ON messages(task_id);

-- 6. analysis_tasks 分析任务表（PRD 4.3，状态机见技术文档 3.6）
CREATE TABLE IF NOT EXISTS analysis_tasks (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id   INTEGER NOT NULL REFERENCES sessions(id),
  message_id   INTEGER NOT NULL REFERENCES messages(id),
  file_id      INTEGER NOT NULL REFERENCES files(id),
  status       TEXT    NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','running','awaiting_clarify','success','failed')),
  stage        TEXT,                            -- intent/plan/executing/retry/chart/report
  intent_json  TEXT,
  plan_json    TEXT,
  result_json  TEXT,
  code_text    TEXT,                            -- 取数过程（F-4.3-8）
  caliber_json TEXT,                            -- 口径说明
  error        TEXT,
  retry_count  INTEGER NOT NULL DEFAULT 0,      -- 上限 3
  duration_ms  INTEGER,
  created_at   TEXT    NOT NULL,
  finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_tasks_session
  ON analysis_tasks(session_id, created_at);
CREATE INDEX IF NOT EXISTS ix_tasks_status
  ON analysis_tasks(status, created_at);
CREATE INDEX IF NOT EXISTS ix_tasks_message
  ON analysis_tasks(message_id);
CREATE INDEX IF NOT EXISTS ix_tasks_file
  ON analysis_tasks(file_id);

-- 7. charts 图表表（PRD 4.4）
CREATE TABLE IF NOT EXISTS charts (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id      INTEGER NOT NULL REFERENCES analysis_tasks(id),
  chart_type   TEXT    NOT NULL,                -- bar/line/pie/scatter
  title        TEXT    NOT NULL,
  option_json  TEXT    NOT NULL,                -- ECharts option
  match_reason TEXT,                            -- 匹配依据（AC-4.4-1）
  created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_charts_task
  ON charts(task_id);

-- 8. reports 分析报告表（PRD 4.5）
CREATE TABLE IF NOT EXISTS reports (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id    INTEGER NOT NULL REFERENCES analysis_tasks(id),
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  title      TEXT    NOT NULL,
  content_md TEXT    NOT NULL,                  -- Markdown 源（导出与页内同源）
  meta_json  TEXT    NOT NULL,                  -- 数据来源/生成时间/口径/耗时
  created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reports_session
  ON reports(session_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uk_reports_task
  ON reports(task_id);   -- 一任务一报告（设计决策③，兜底接口幂等契约）

-- 9. feedbacks 满意度反馈表（F-4.6-7）
CREATE TABLE IF NOT EXISTS feedbacks (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL REFERENCES users(id),
  message_id INTEGER NOT NULL REFERENCES messages(id),
  task_id    INTEGER REFERENCES analysis_tasks(id),   -- 统计归因冗余
  value      TEXT    NOT NULL CHECK (value IN ('up','down')),
  created_at TEXT    NOT NULL,
  CONSTRAINT uk_feedback_user_message UNIQUE (user_id, message_id)
);
CREATE INDEX IF NOT EXISTS ix_feedback_value
  ON feedbacks(value, created_at);
CREATE INDEX IF NOT EXISTS ix_feedback_task
  ON feedbacks(task_id);

-- 10. daily_statistics 日聚合统计表（PRD 4.8）
CREATE TABLE IF NOT EXISTS daily_statistics (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  stat_date  TEXT    NOT NULL,                  -- YYYY-MM-DD
  scope      TEXT    NOT NULL CHECK (scope IN ('user','global')),
  user_id    INTEGER REFERENCES users(id),      -- scope='user' 必填；global 为 NULL
  metric     TEXT    NOT NULL,                  -- task_count/success_rate/chart_count/...
  value      REAL    NOT NULL,
  created_at TEXT    NOT NULL
);
-- 唯一维度约束：用 IFNULL 归一 user_id，保证 scope='global'（user_id 为 NULL）行同样去重
CREATE UNIQUE INDEX IF NOT EXISTS uk_stat_dim
  ON daily_statistics(stat_date, scope, IFNULL(user_id, 0), metric);

-- 11. audit_logs 操作审计日志表（F-4.9-5，仅追加）
CREATE TABLE IF NOT EXISTS audit_logs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  operator_id INTEGER NOT NULL REFERENCES users(id),
  action      TEXT    NOT NULL,                 -- user_disable/user_enable/session_view/...
  target_type TEXT    NOT NULL,                 -- user/session/report
  target_id   INTEGER,
  detail_json TEXT,                             -- 脱敏后写入
  ip          TEXT,
  created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_time
  ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS ix_audit_action
  ON audit_logs(action, created_at);

-- 12. sensitive_words 敏感词表（F-4.6-6，独立配置表，无业务外键）
CREATE TABLE IF NOT EXISTS sensitive_words (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  word       TEXT    NOT NULL UNIQUE,
  category   TEXT    NOT NULL,                  -- 政治/色情/暴恐/广告等
  enabled    INTEGER NOT NULL DEFAULT 1,
  created_at TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_sensitive_words_word
  ON sensitive_words(word);

-- 13. configs 系统配置表（技术文档 3.5/10.1，业务主键）
CREATE TABLE IF NOT EXISTS configs (
  key        TEXT PRIMARY KEY,                  -- llm.provider / llm.api_key / ...
  value      TEXT    NOT NULL,                  -- api_key 为 AES-GCM 密文（NFR-S2）
  updated_at TEXT    NOT NULL
);
