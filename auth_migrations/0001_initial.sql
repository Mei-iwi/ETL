CREATE TABLE auth_roles (
	id SERIAL NOT NULL,
	code VARCHAR(20) NOT NULL,
	name VARCHAR(80) NOT NULL,
	description VARCHAR(255) NOT NULL,
	created_at FLOAT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_auth_role_code CHECK (code IN ('admin', 'operator', 'viewer')),
	UNIQUE (code)
);

CREATE TABLE auth_schema_versions (
	version SERIAL NOT NULL,
	PRIMARY KEY (version)
);

CREATE TABLE auth_users (
	id UUID NOT NULL,
	email VARCHAR(254) NOT NULL,
	display_name VARCHAR(120) NOT NULL,
	password_hash VARCHAR(255) NOT NULL,
	is_active BOOLEAN NOT NULL,
	is_locked BOOLEAN NOT NULL,
	locked_until FLOAT,
	failed_login_count INTEGER NOT NULL,
	must_change_password BOOLEAN NOT NULL,
	auth_version INTEGER NOT NULL,
	permissions JSON NOT NULL,
	resource_ids JSON NOT NULL,
	last_login_at FLOAT,
	password_changed_at FLOAT NOT NULL,
	created_at FLOAT NOT NULL,
	updated_at FLOAT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_auth_email_normalized CHECK (email = lower(trim(email))),
	CONSTRAINT ck_auth_password_argon2id CHECK (password_hash LIKE '$argon2id$%%'),
	CONSTRAINT ck_auth_user_counters CHECK (auth_version > 0 AND failed_login_count >= 0),
	UNIQUE (email)
);

CREATE TABLE auth_user_roles (
	user_id UUID NOT NULL,
	role_id INTEGER NOT NULL,
	PRIMARY KEY (user_id, role_id),
	FOREIGN KEY(user_id) REFERENCES auth_users (id) ON DELETE RESTRICT,
	FOREIGN KEY(role_id) REFERENCES auth_roles (id) ON DELETE RESTRICT
);
