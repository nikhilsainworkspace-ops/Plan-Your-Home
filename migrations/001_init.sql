PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  full_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'homeowner' CHECK(role IN ('homeowner','architect','civil_engineer','contractor','interior_designer','other')),
  units TEXT NOT NULL DEFAULT 'metric' CHECK(units IN ('metric','imperial')),
  currency TEXT NOT NULL DEFAULT 'INR',
  trial_started_at TEXT,
  trial_ends_at TEXT,
  trial_status TEXT NOT NULL DEFAULT 'not_started' CHECK(trial_status IN ('not_started','trialing','expired','converted')),
  is_admin INTEGER NOT NULL DEFAULT 0 CHECK(is_admin IN (0,1)),
  onboarding_complete INTEGER NOT NULL DEFAULT 0 CHECK(onboarding_complete IN (0,1)),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash TEXT NOT NULL UNIQUE,
  csrf_token TEXT NOT NULL,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
CREATE TABLE IF NOT EXISTS password_resets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS demo_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_demo_expiry ON demo_sessions(expires_at);
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  demo_session_id INTEGER REFERENCES demo_sessions(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  project_type TEXT NOT NULL DEFAULT 'new_home' CHECK(project_type IN ('new_home','renovation','extension','interior_remodel','room_refresh')),
  description TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '',
  plot_length_m REAL NOT NULL DEFAULT 20 CHECK(plot_length_m >= 0),
  plot_width_m REAL NOT NULL DEFAULT 15 CHECK(plot_width_m >= 0),
  floors INTEGER NOT NULL DEFAULT 1 CHECK(floors BETWEEN 1 AND 20),
  built_area_m2 REAL NOT NULL DEFAULT 120 CHECK(built_area_m2 >= 0),
  budget REAL NOT NULL DEFAULT 0 CHECK(budget >= 0),
  currency TEXT NOT NULL DEFAULT 'INR',
  status TEXT NOT NULL DEFAULT 'planning' CHECK(status IN ('planning','in_progress','on_hold','completed','archived')),
  notes TEXT NOT NULL DEFAULT '',
  start_date TEXT,
  target_date TEXT,
  archived_at TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  CHECK ((owner_id IS NOT NULL AND demo_session_id IS NULL) OR (owner_id IS NULL AND demo_session_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_projects_owner_updated ON projects(owner_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_projects_demo ON projects(demo_session_id);
CREATE TABLE IF NOT EXISTS rooms (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  room_type TEXT NOT NULL DEFAULT 'other',
  width_m REAL NOT NULL DEFAULT 3 CHECK(width_m > 0 AND width_m <= 100),
  depth_m REAL NOT NULL DEFAULT 3 CHECK(depth_m > 0 AND depth_m <= 100),
  x_m REAL NOT NULL DEFAULT 0 CHECK(x_m >= 0 AND x_m <= 500),
  y_m REAL NOT NULL DEFAULT 0 CHECK(y_m >= 0 AND y_m <= 500),
  floor_no INTEGER NOT NULL DEFAULT 1 CHECK(floor_no BETWEEN 1 AND 20),
  color TEXT NOT NULL DEFAULT '#DDEAE4',
  doors INTEGER NOT NULL DEFAULT 1 CHECK(doors BETWEEN 0 AND 20),
  windows INTEGER NOT NULL DEFAULT 1 CHECK(windows BETWEEN 0 AND 50),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_rooms_project ON rooms(project_id);
CREATE TABLE IF NOT EXISTS estimates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
  cost_per_m2 REAL NOT NULL DEFAULT 18000 CHECK(cost_per_m2 >= 0),
  contingency_pct REAL NOT NULL DEFAULT 10 CHECK(contingency_pct BETWEEN 0 AND 100),
  finish_level TEXT NOT NULL DEFAULT 'standard' CHECK(finish_level IN ('economy','standard','premium')),
  assumptions TEXT NOT NULL DEFAULT 'Indicative planning assumption; replace with verified local quotes.',
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS estimate_lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  category TEXT NOT NULL,
  pct REAL NOT NULL DEFAULT 0 CHECK(pct BETWEEN 0 AND 100),
  notes TEXT NOT NULL DEFAULT '',
  UNIQUE(project_id, category)
);
CREATE TABLE IF NOT EXISTS materials (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  quantity REAL NOT NULL DEFAULT 1 CHECK(quantity >= 0),
  unit TEXT NOT NULL DEFAULT 'unit',
  unit_cost REAL NOT NULL DEFAULT 0 CHECK(unit_cost >= 0),
  supplier TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS expenses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  category TEXT NOT NULL,
  description TEXT NOT NULL,
  amount REAL NOT NULL CHECK(amount >= 0),
  spent_on TEXT NOT NULL DEFAULT (date('now')),
  vendor TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'todo' CHECK(status IN ('todo','in_progress','done')),
  priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('low','normal','high')),
  due_date TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_tasks_project_status ON tasks(project_id, status);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  uploader_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  original_name TEXT NOT NULL,
  stored_name TEXT NOT NULL UNIQUE,
  content_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  demo_session_id INTEGER REFERENCES demo_sessions(id) ON DELETE CASCADE,
  project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
  action TEXT NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  CHECK ((owner_id IS NOT NULL AND demo_session_id IS NULL) OR (owner_id IS NULL AND demo_session_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_activity_owner ON activity(owner_id, created_at DESC);
CREATE TABLE IF NOT EXISTS contact_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS subscriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider TEXT NOT NULL DEFAULT 'razorpay',
  provider_subscription_id TEXT UNIQUE,
  plan_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','authenticated','active','past_due','halted','cancelled','completed','expired')),
  current_period_end TEXT,
  checkout_url TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions(user_id, status);
CREATE TABLE IF NOT EXISTS billing_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider_event_id TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL,
  processed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  action TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_id TEXT,
  details TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);

CREATE TABLE IF NOT EXISTS login_attempts (
  key_hash TEXT PRIMARY KEY,
  attempts INTEGER NOT NULL DEFAULT 0,
  window_started_at TEXT NOT NULL
);
