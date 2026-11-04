-- MASTER schema, PostgreSQL 16, revision 0020.
-- Exported from the current database; contains no business rows or credentials.
-- Restore into an EMPTY database only, using psql -X -v ON_ERROR_STOP=1 --single-transaction.
-- Alternative to alembic upgrade 0020; do not import both into the same database.
-- All existing public tables remain referenced by the current application/migrations.
-- The disabled revision 0021 master_component_* tables are intentionally absent.
-- For a full data restore, use the separate private custom-format .dump instead.

--
-- PostgreSQL database dump
--

\restrict 9dyM8JdBsTmhcOfxY7KIIhcT1ZYrcdeS2wu9kAGDTZEu0nqeEpSrYLoJ7nvmZap

-- Dumped from database version 16.15 (Debian 16.15-1.pgdg12+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg12+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- Name: master_guard_content_parent(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.master_guard_content_parent() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO '$user', 'public'
    AS $$
            BEGIN
                IF NEW.parent_unit_id IS NOT NULL THEN
                    IF current_setting('transaction_isolation') <> 'read committed' THEN
                        RAISE EXCEPTION 'Content parent writes require READ COMMITTED; retry transaction'
                            USING ERRCODE='25001';
                    END IF;
                    PERFORM pg_advisory_xact_lock(hashtextextended('content-parent:' || NEW.resource_version_id, 0));
                    IF EXISTS (
                        WITH RECURSIVE ancestors AS (
                            SELECT id,parent_unit_id FROM content_units WHERE id=NEW.parent_unit_id
                            UNION
                            SELECT c.id,c.parent_unit_id FROM content_units c JOIN ancestors a ON c.id=a.parent_unit_id
                        ) SELECT 1 FROM ancestors WHERE id=NEW.id
                    ) THEN
                        RAISE EXCEPTION 'Content unit parent cycle' USING ERRCODE='23514';
                    END IF;
                END IF;
                RETURN NEW;
            END $$;


--
-- Name: FUNCTION master_guard_content_parent(); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.master_guard_content_parent() IS 'Chặn chu kỳ parent; chỉ duyệt tổ tiên khi parent khác NULL, khóa theo version tránh hai giao dịch tạo vòng đồng thời.';


--
-- Name: master_set_local_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.master_set_local_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            IF NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'Runtime created_at is immutable after insert' USING ERRCODE='23514';
            END IF;
            IF (to_jsonb(NEW) - ARRAY['created_at','updated_at']) IS DISTINCT FROM
               (to_jsonb(OLD) - ARRAY['created_at','updated_at']) THEN
                NEW.updated_at := GREATEST(clock_timestamp(), OLD.updated_at + INTERVAL '1 microsecond', NEW.updated_at);
            ELSE
                NEW.updated_at := GREATEST(OLD.updated_at, NEW.updated_at);
            END IF;
            RETURN NEW;
        END $$;


--
-- Name: FUNCTION master_set_local_updated_at(); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.master_set_local_updated_at() IS '0007: giữ created_at và timestamp runtime không lùi; không áp dụng lên dữ liệu Master nguồn.';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: content_unit_texts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.content_unit_texts (
    content_unit_id character varying(30) NOT NULL,
    page_result_id character varying(30) NOT NULL,
    text text NOT NULL
);


--
-- Name: content_units; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.content_units (
    id character varying(30) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    resource_version_id character varying(30) NOT NULL,
    parent_unit_id character varying(30),
    unit_type character varying(30) NOT NULL,
    title character varying(500),
    sequence_no integer NOT NULL,
    page_from integer,
    page_to integer,
    review_status character varying(30) NOT NULL,
    retrieval_eligible boolean NOT NULL,
    mongo_id character varying(30),
    source_locator jsonb,
    CONSTRAINT ck_content_parent_not_self CHECK (((parent_unit_id)::text <> (id)::text)),
    CONSTRAINT content_units_check CHECK (((page_from > 0) AND (page_to >= page_from))),
    CONSTRAINT content_units_sequence_no_check CHECK ((sequence_no > 0))
);


--
-- Name: etl_stage_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.etl_stage_runs (
    id character varying(30) NOT NULL,
    resource_version_id character varying(30) NOT NULL,
    stage character varying(30) NOT NULL,
    status character varying(30) NOT NULL,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    input_fingerprint character varying(64),
    error_message text,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: master_grade_levels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.master_grade_levels (
    id character varying(30) NOT NULL,
    education_level_id character varying(30) NOT NULL,
    grade_code character varying(50) NOT NULL,
    grade_name_vi character varying(100) NOT NULL,
    grade_ordinal smallint,
    grade_created_by character varying(30),
    grade_updated_by character varying(30),
    grade_is_active boolean NOT NULL,
    grade_deleted_at timestamp with time zone,
    grade_deleted_by character varying(30),
    grade_created_at timestamp with time zone,
    grade_updated_at timestamp with time zone,
    education_code character varying(30) NOT NULL,
    education_name_vi character varying(100) NOT NULL,
    education_ordinal smallint,
    education_is_active boolean NOT NULL,
    education_deleted_at timestamp with time zone,
    education_deleted_by character varying(30),
    education_created_at timestamp with time zone,
    education_updated_at timestamp with time zone,
    grade_mongo_id character varying(24),
    source_origin character varying(30) DEFAULT 'UNKNOWN'::character varying NOT NULL,
    source_state_hash character varying(64),
    source_missing_at timestamp with time zone,
    source_identity_version smallint,
    source_backend character varying(64),
    source_namespace character varying(255),
    source_entity_type character varying(64),
    source_record_key character varying(255),
    source_identity_hash character varying(64)
);


--
-- Name: master_learning_resources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.master_learning_resources (
    id character varying(30) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    provider_id character varying(30),
    provider_name character varying(255),
    provider_type character varying(30),
    provider_is_active boolean,
    provider_deleted_at timestamp with time zone,
    provider_deleted_by character varying(30),
    provider_created_at timestamp with time zone,
    provider_updated_at timestamp with time zone,
    resource_type_id character varying(30) NOT NULL,
    resource_type_code character varying(50) NOT NULL,
    resource_type_name_vi character varying(150) NOT NULL,
    resource_type_is_active boolean NOT NULL,
    resource_type_deleted_at timestamp with time zone,
    resource_type_deleted_by character varying(30),
    resource_type_created_at timestamp with time zone,
    resource_type_updated_at timestamp with time zone,
    created_by character varying(30),
    title character varying(500),
    publication_status character varying(30) NOT NULL,
    is_active boolean NOT NULL,
    deleted_at timestamp with time zone,
    deleted_by character varying(30),
    mongo_id character varying(30),
    source_origin character varying(30) DEFAULT 'UNKNOWN'::character varying NOT NULL,
    source_state_hash character varying(64),
    source_missing_at timestamp with time zone,
    master_deleted_at timestamp with time zone,
    master_deleted_by character varying(255),
    master_deletion_reason character varying(500),
    deletion_revision integer DEFAULT 0 NOT NULL,
    source_identity_version smallint,
    source_backend character varying(64),
    source_namespace character varying(255),
    source_entity_type character varying(64),
    source_record_key character varying(255),
    source_identity_hash character varying(64)
);


--
-- Name: master_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.master_users (
    id character varying(30) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    email character varying(255) NOT NULL,
    hashed_password character varying(255) NOT NULL,
    full_name character varying(255),
    role_id character varying(30) NOT NULL,
    role_code character varying(50) NOT NULL,
    role_name character varying(255) NOT NULL,
    role_description character varying(255),
    role_is_active boolean NOT NULL,
    role_deleted_at timestamp with time zone,
    role_deleted_by character varying(30),
    role_created_at timestamp with time zone,
    role_updated_at timestamp with time zone,
    is_active boolean NOT NULL,
    email_verified_at timestamp with time zone,
    deleted_at timestamp with time zone,
    deleted_by character varying(30),
    mongo_id character varying(30),
    source_origin character varying(30) DEFAULT 'UNKNOWN'::character varying NOT NULL,
    source_state_hash character varying(64),
    source_missing_at timestamp with time zone,
    source_identity_version smallint,
    source_backend character varying(64),
    source_namespace character varying(255),
    source_entity_type character varying(64),
    source_record_key character varying(255),
    source_identity_hash character varying(64)
);


--
-- Name: ocr_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ocr_jobs (
    id character varying(30) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    resource_version_id character varying(30) NOT NULL,
    triggered_by character varying(30),
    status character varying(30) NOT NULL,
    total_pages integer NOT NULL,
    completed_pages integer NOT NULL,
    failed_pages integer NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    postprocess_enqueued_at timestamp with time zone,
    CONSTRAINT ocr_jobs_check CHECK (((total_pages >= 0) AND (completed_pages >= 0) AND (failed_pages >= 0) AND ((completed_pages + failed_pages) <= total_pages)))
);


--
-- Name: ocr_page_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ocr_page_results (
    id character varying(30) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    page_task_id character varying(30) NOT NULL,
    raw_text text NOT NULL,
    normalized_text text,
    confidence numeric,
    engine_name character varying(100) NOT NULL,
    engine_version character varying(100),
    result_metadata jsonb
);


--
-- Name: ocr_page_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ocr_page_tasks (
    id character varying(30) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    job_id character varying(30) NOT NULL,
    page_num integer NOT NULL,
    local_image_path character varying(500),
    status character varying(30) NOT NULL,
    worker_id character varying(100),
    retry_count integer NOT NULL,
    error_message text,
    heartbeat_at timestamp with time zone,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    claim_token character varying(30),
    CONSTRAINT ocr_page_tasks_check CHECK (((page_num > 0) AND (retry_count >= 0)))
);


--
-- Name: reconciliation_audit_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reconciliation_audit_jobs (
    id character varying(36) NOT NULL,
    job_key character varying(150) NOT NULL,
    audit_type character varying(30) NOT NULL,
    stream_name character varying(30) NOT NULL,
    status character varying(30) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    claim_token character varying(36),
    lease_expires_at timestamp with time zone,
    heartbeat_at timestamp with time zone,
    next_retry_at timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    checkpoint jsonb NOT NULL,
    error_code character varying(80)
);


--
-- Name: reconciliation_file_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reconciliation_file_states (
    resource_version_id character varying(36) NOT NULL,
    last_verified_at timestamp with time zone,
    last_verified_hash character varying(64),
    last_verified_size bigint,
    verification_method character varying(40),
    observed_identity character varying(64),
    last_observed_revision character varying(64),
    last_attempt_at timestamp with time zone,
    next_due_at timestamp with time zone,
    status character varying(30) NOT NULL,
    reason_code character varying(80),
    attempts integer DEFAULT 0 NOT NULL
);


--
-- Name: resource_deletion_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.resource_deletion_events (
    id character varying(30) NOT NULL,
    resource_id character varying(30) NOT NULL,
    revision integer NOT NULL,
    action character varying(10) NOT NULL,
    actor_id character varying(255) NOT NULL,
    reason character varying(500),
    origin character varying(30) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    propagated_at timestamp with time zone,
    CONSTRAINT ck_resource_deletion_action CHECK (((action)::text = ANY ((ARRAY['DELETE'::character varying, 'RESTORE'::character varying])::text[]))),
    CONSTRAINT ck_resource_deletion_positive_revision CHECK ((revision > 0))
);


--
-- Name: resource_grade_levels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.resource_grade_levels (
    resource_id character varying(30) NOT NULL,
    grade_level_id character varying(30) NOT NULL
);


--
-- Name: resource_subjects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.resource_subjects (
    resource_id character varying(30) NOT NULL,
    subject_id character varying(30) NOT NULL,
    is_primary boolean NOT NULL
);


--
-- Name: resource_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.resource_versions (
    id character varying(30) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    resource_id character varying(30) NOT NULL,
    parent_version_id character varying(30),
    version_number integer NOT NULL,
    lifecycle_status character varying(30) NOT NULL,
    storage_bucket character varying(255) NOT NULL,
    storage_object_key character varying(500) NOT NULL,
    source_hash character varying(64) NOT NULL,
    uploaded_by character varying(30),
    assigned_by character varying(30),
    assigned_reviewer_id character varying(30),
    assigned_at timestamp with time zone,
    pre_ocr_approved_by character varying(30),
    pre_ocr_approved_at timestamp with time zone,
    final_approved_by character varying(30),
    final_approved_at timestamp with time zone,
    rejection_reason text,
    mongo_id character varying(30),
    source_file_size bigint,
    storage_file_size bigint,
    processing_options jsonb,
    CONSTRAINT resource_versions_version_number_check CHECK ((version_number > 0))
);


--
-- Name: source_file_imports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_file_imports (
    id character varying(30) NOT NULL,
    source_system character varying(30) NOT NULL,
    source_version_id character varying(30) NOT NULL,
    resource_id character varying(30) NOT NULL,
    resource_title character varying(500),
    source_version_number integer NOT NULL,
    source_lifecycle_status character varying(30) NOT NULL,
    source_resource_active boolean NOT NULL,
    source_updated_at timestamp with time zone NOT NULL,
    storage_bucket character varying(255) NOT NULL,
    storage_object_key character varying(500) NOT NULL,
    source_hash character varying(64) NOT NULL,
    status character varying(30) NOT NULL,
    attempts integer NOT NULL,
    manual_retries integer NOT NULL,
    processing_retry_requested boolean NOT NULL,
    next_retry_at timestamp with time zone,
    last_error_code character varying(50),
    claim_token character varying(30),
    lease_expires_at timestamp with time zone,
    target_version_id character varying(30),
    processing_job_id character varying(30),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    source_file_size bigint,
    storage_file_size bigint
);


--
-- Name: subjects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subjects (
    id character varying(30) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    grade_id character varying(30),
    subject_name character varying(255) NOT NULL,
    subject_slug character varying(255) NOT NULL,
    is_active boolean NOT NULL,
    deleted_at timestamp with time zone,
    deleted_by character varying(30),
    source_origin character varying(30) DEFAULT 'UNKNOWN'::character varying NOT NULL,
    source_state_hash character varying(64),
    source_missing_at timestamp with time zone,
    source_identity_version smallint,
    source_backend character varying(64),
    source_namespace character varying(255),
    source_entity_type character varying(64),
    source_record_key character varying(255),
    source_identity_hash character varying(64)
);


--
-- Name: sync_auto_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sync_auto_config (
    id character varying(30) NOT NULL,
    enabled boolean NOT NULL,
    interval_seconds integer NOT NULL,
    next_run_at timestamp with time zone,
    worker_id character varying(100),
    heartbeat_at timestamp with time zone,
    source_ready boolean NOT NULL,
    lease_owner character varying(100),
    lease_expires_at timestamp with time zone,
    last_auto_run_at timestamp with time zone,
    last_error_code character varying(50),
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: sync_auto_streams; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sync_auto_streams (
    stream_name character varying(30) NOT NULL,
    config_id character varying(30) NOT NULL,
    enabled boolean NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    sync_mode character varying(20) DEFAULT 'INCREMENTAL'::character varying NOT NULL
);


--
-- Name: sync_checkpoints; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sync_checkpoints (
    stream_name character varying(30) NOT NULL,
    last_updated_at timestamp with time zone,
    last_id character varying(30),
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: sync_reconciliation_discrepancies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sync_reconciliation_discrepancies (
    id character varying(36) NOT NULL,
    run_id character varying(36) NOT NULL,
    record_id character varying(100) NOT NULL,
    stream_name character varying(50) NOT NULL,
    discrepancy_flag character varying(50) NOT NULL,
    source_value jsonb,
    target_value jsonb,
    detected_at timestamp with time zone NOT NULL,
    resolution_status character varying(30) DEFAULT 'UNRESOLVED'::character varying NOT NULL
);


--
-- Name: sync_reconciliation_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sync_reconciliation_runs (
    run_id character varying(36) NOT NULL,
    stream_name character varying(50) NOT NULL,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    status character varying(30) NOT NULL,
    source_count integer DEFAULT 0 NOT NULL,
    target_count integer DEFAULT 0 NOT NULL,
    matched_count integer DEFAULT 0 NOT NULL,
    mismatched_count integer DEFAULT 0 NOT NULL,
    source_total_bytes bigint DEFAULT '0'::bigint,
    target_total_bytes bigint DEFAULT '0'::bigint,
    source_table_hash character varying(64),
    target_table_hash character varying(64),
    summary_meta jsonb,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: sync_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sync_runs (
    id character varying(30) NOT NULL,
    stream_name character varying(30) NOT NULL,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    status character varying(30) NOT NULL,
    records_read integer NOT NULL,
    inserted integer NOT NULL,
    updated integer NOT NULL,
    skipped integer NOT NULL,
    failed integer NOT NULL,
    checkpoint_before jsonb,
    checkpoint_after jsonb,
    error_message text,
    sync_mode character varying(20)
);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: content_unit_texts content_unit_texts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_unit_texts
    ADD CONSTRAINT content_unit_texts_pkey PRIMARY KEY (content_unit_id);


--
-- Name: content_units content_units_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_units
    ADD CONSTRAINT content_units_pkey PRIMARY KEY (id);


--
-- Name: content_units content_units_resource_version_id_sequence_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_units
    ADD CONSTRAINT content_units_resource_version_id_sequence_no_key UNIQUE (resource_version_id, sequence_no);


--
-- Name: etl_stage_runs etl_stage_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.etl_stage_runs
    ADD CONSTRAINT etl_stage_runs_pkey PRIMARY KEY (id);


--
-- Name: master_grade_levels master_grade_levels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.master_grade_levels
    ADD CONSTRAINT master_grade_levels_pkey PRIMARY KEY (id);


--
-- Name: master_learning_resources master_learning_resources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.master_learning_resources
    ADD CONSTRAINT master_learning_resources_pkey PRIMARY KEY (id);


--
-- Name: master_users master_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.master_users
    ADD CONSTRAINT master_users_pkey PRIMARY KEY (id);


--
-- Name: ocr_jobs ocr_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ocr_jobs
    ADD CONSTRAINT ocr_jobs_pkey PRIMARY KEY (id);


--
-- Name: ocr_page_results ocr_page_results_page_task_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ocr_page_results
    ADD CONSTRAINT ocr_page_results_page_task_id_key UNIQUE (page_task_id);


--
-- Name: ocr_page_results ocr_page_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ocr_page_results
    ADD CONSTRAINT ocr_page_results_pkey PRIMARY KEY (id);


--
-- Name: ocr_page_tasks ocr_page_tasks_job_id_page_num_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ocr_page_tasks
    ADD CONSTRAINT ocr_page_tasks_job_id_page_num_key UNIQUE (job_id, page_num);


--
-- Name: ocr_page_tasks ocr_page_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ocr_page_tasks
    ADD CONSTRAINT ocr_page_tasks_pkey PRIMARY KEY (id);


--
-- Name: reconciliation_audit_jobs reconciliation_audit_jobs_job_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconciliation_audit_jobs
    ADD CONSTRAINT reconciliation_audit_jobs_job_key_key UNIQUE (job_key);


--
-- Name: reconciliation_audit_jobs reconciliation_audit_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconciliation_audit_jobs
    ADD CONSTRAINT reconciliation_audit_jobs_pkey PRIMARY KEY (id);


--
-- Name: reconciliation_file_states reconciliation_file_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconciliation_file_states
    ADD CONSTRAINT reconciliation_file_states_pkey PRIMARY KEY (resource_version_id);


--
-- Name: resource_deletion_events resource_deletion_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_deletion_events
    ADD CONSTRAINT resource_deletion_events_pkey PRIMARY KEY (id);


--
-- Name: resource_grade_levels resource_grade_levels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_grade_levels
    ADD CONSTRAINT resource_grade_levels_pkey PRIMARY KEY (resource_id, grade_level_id);


--
-- Name: resource_subjects resource_subjects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_subjects
    ADD CONSTRAINT resource_subjects_pkey PRIMARY KEY (resource_id, subject_id);


--
-- Name: resource_versions resource_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_versions
    ADD CONSTRAINT resource_versions_pkey PRIMARY KEY (id);


--
-- Name: resource_versions resource_versions_resource_id_version_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_versions
    ADD CONSTRAINT resource_versions_resource_id_version_number_key UNIQUE (resource_id, version_number);


--
-- Name: source_file_imports source_file_imports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_file_imports
    ADD CONSTRAINT source_file_imports_pkey PRIMARY KEY (id);


--
-- Name: subjects subjects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subjects
    ADD CONSTRAINT subjects_pkey PRIMARY KEY (id);


--
-- Name: sync_auto_config sync_auto_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_auto_config
    ADD CONSTRAINT sync_auto_config_pkey PRIMARY KEY (id);


--
-- Name: sync_auto_streams sync_auto_streams_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_auto_streams
    ADD CONSTRAINT sync_auto_streams_pkey PRIMARY KEY (stream_name);


--
-- Name: sync_checkpoints sync_checkpoints_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_checkpoints
    ADD CONSTRAINT sync_checkpoints_pkey PRIMARY KEY (stream_name);


--
-- Name: sync_reconciliation_discrepancies sync_reconciliation_discrepancies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_reconciliation_discrepancies
    ADD CONSTRAINT sync_reconciliation_discrepancies_pkey PRIMARY KEY (id);


--
-- Name: sync_reconciliation_runs sync_reconciliation_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_reconciliation_runs
    ADD CONSTRAINT sync_reconciliation_runs_pkey PRIMARY KEY (run_id);


--
-- Name: sync_runs sync_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_runs
    ADD CONSTRAINT sync_runs_pkey PRIMARY KEY (id);


--
-- Name: content_units uq_content_unit_version_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_units
    ADD CONSTRAINT uq_content_unit_version_id UNIQUE (resource_version_id, id);


--
-- Name: resource_deletion_events uq_resource_deletion_revision; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_deletion_events
    ADD CONSTRAINT uq_resource_deletion_revision UNIQUE (resource_id, revision);


--
-- Name: source_file_imports uq_source_file_identity; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_file_imports
    ADD CONSTRAINT uq_source_file_identity UNIQUE (source_system, source_version_id);


--
-- Name: source_file_imports uq_source_file_target; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_file_imports
    ADD CONSTRAINT uq_source_file_target UNIQUE (target_version_id);


--
-- Name: ix_audit_file_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_file_due ON public.reconciliation_file_states USING btree (next_due_at, resource_version_id);


--
-- Name: ix_audit_jobs_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_jobs_due ON public.reconciliation_audit_jobs USING btree (status, next_retry_at);


--
-- Name: ix_reconcile_discrepancies_record; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reconcile_discrepancies_record ON public.sync_reconciliation_discrepancies USING btree (record_id);


--
-- Name: ix_reconcile_discrepancies_run_flag; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reconcile_discrepancies_run_flag ON public.sync_reconciliation_discrepancies USING btree (run_id, discrepancy_flag);


--
-- Name: ix_reconcile_discrepancies_stream_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reconcile_discrepancies_stream_status ON public.sync_reconciliation_discrepancies USING btree (stream_name, resolution_status);


--
-- Name: ix_reconcile_runs_stream_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reconcile_runs_stream_started ON public.sync_reconciliation_runs USING btree (stream_name, started_at DESC);


--
-- Name: ix_resource_deletion_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_resource_deletion_pending ON public.resource_deletion_events USING btree (propagated_at, created_at);


--
-- Name: ix_resource_subjects_subject; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_resource_subjects_subject ON public.resource_subjects USING btree (subject_id);


--
-- Name: ix_source_file_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_file_queue ON public.source_file_imports USING btree (status, next_retry_at, lease_expires_at);


--
-- Name: ix_stage_version; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_stage_version ON public.etl_stage_runs USING btree (resource_version_id, stage, created_at);


--
-- Name: ix_subject_grade_active_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_subject_grade_active_deleted ON public.subjects USING btree (grade_id, is_active, deleted_at);


--
-- Name: ix_subjects_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_subjects_name ON public.subjects USING btree (subject_name);


--
-- Name: ix_subjects_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_subjects_slug ON public.subjects USING btree (subject_slug);


--
-- Name: ix_tasks_claim; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_claim ON public.ocr_page_tasks USING btree (status, created_at);


--
-- Name: ix_tasks_heartbeat; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_heartbeat ON public.ocr_page_tasks USING btree (status, heartbeat_at);


--
-- Name: ix_versions_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_versions_hash ON public.resource_versions USING btree (source_hash);


--
-- Name: ix_versions_resource_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_versions_resource_created ON public.resource_versions USING btree (resource_id, created_at);


--
-- Name: uq_active_ocr_version; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_active_ocr_version ON public.ocr_jobs USING btree (resource_version_id) WHERE ((status)::text = ANY ((ARRAY['PENDING'::character varying, 'RUNNING'::character varying])::text[]));


--
-- Name: uq_content_units_mongo_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_content_units_mongo_id ON public.content_units USING btree (mongo_id);


--
-- Name: uq_master_grade_levels_source_identity; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_master_grade_levels_source_identity ON public.master_grade_levels USING btree (source_namespace, source_backend, source_entity_type, source_record_key);


--
-- Name: uq_master_learning_resources_mongo_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_master_learning_resources_mongo_id ON public.master_learning_resources USING btree (mongo_id);


--
-- Name: uq_master_learning_resources_source_identity; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_master_learning_resources_source_identity ON public.master_learning_resources USING btree (source_namespace, source_backend, source_entity_type, source_record_key);


--
-- Name: uq_master_users_mongo_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_master_users_mongo_id ON public.master_users USING btree (mongo_id);


--
-- Name: uq_master_users_source_identity; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_master_users_source_identity ON public.master_users USING btree (source_namespace, source_backend, source_entity_type, source_record_key);


--
-- Name: uq_resource_versions_mongo_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_resource_versions_mongo_id ON public.resource_versions USING btree (mongo_id);


--
-- Name: uq_subjects_source_identity; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_subjects_source_identity ON public.subjects USING btree (source_namespace, source_backend, source_entity_type, source_record_key);


--
-- Name: content_units trg_content_parent_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_content_parent_guard BEFORE INSERT OR UPDATE OF parent_unit_id, resource_version_id ON public.content_units FOR EACH ROW EXECUTE FUNCTION public.master_guard_content_parent();


--
-- Name: TRIGGER trg_content_parent_guard ON content_units; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TRIGGER trg_content_parent_guard ON public.content_units IS 'Chặn vòng lặp parent; khóa theo version và kiểm tra tổ tiên trong READ COMMITTED.';


--
-- Name: content_units trg_content_units_local_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_content_units_local_updated_at BEFORE UPDATE ON public.content_units FOR EACH ROW EXECUTE FUNCTION public.master_set_local_updated_at();


--
-- Name: TRIGGER trg_content_units_local_updated_at ON content_units; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TRIGGER trg_content_units_local_updated_at ON public.content_units IS 'Cập nhật timestamp runtime khi application chưa cập nhật; không áp dụng lên master_*.';


--
-- Name: ocr_jobs trg_ocr_jobs_local_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_ocr_jobs_local_updated_at BEFORE UPDATE ON public.ocr_jobs FOR EACH ROW EXECUTE FUNCTION public.master_set_local_updated_at();


--
-- Name: TRIGGER trg_ocr_jobs_local_updated_at ON ocr_jobs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TRIGGER trg_ocr_jobs_local_updated_at ON public.ocr_jobs IS 'Cập nhật timestamp runtime khi application chưa cập nhật; không áp dụng lên master_*.';


--
-- Name: ocr_page_results trg_ocr_page_results_local_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_ocr_page_results_local_updated_at BEFORE UPDATE ON public.ocr_page_results FOR EACH ROW EXECUTE FUNCTION public.master_set_local_updated_at();


--
-- Name: TRIGGER trg_ocr_page_results_local_updated_at ON ocr_page_results; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TRIGGER trg_ocr_page_results_local_updated_at ON public.ocr_page_results IS 'Cập nhật timestamp runtime khi application chưa cập nhật; không áp dụng lên master_*.';


--
-- Name: ocr_page_tasks trg_ocr_page_tasks_local_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_ocr_page_tasks_local_updated_at BEFORE UPDATE ON public.ocr_page_tasks FOR EACH ROW EXECUTE FUNCTION public.master_set_local_updated_at();


--
-- Name: TRIGGER trg_ocr_page_tasks_local_updated_at ON ocr_page_tasks; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TRIGGER trg_ocr_page_tasks_local_updated_at ON public.ocr_page_tasks IS 'Cập nhật timestamp runtime khi application chưa cập nhật; không áp dụng lên master_*.';


--
-- Name: resource_versions trg_resource_versions_local_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_resource_versions_local_updated_at BEFORE UPDATE ON public.resource_versions FOR EACH ROW EXECUTE FUNCTION public.master_set_local_updated_at();


--
-- Name: TRIGGER trg_resource_versions_local_updated_at ON resource_versions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TRIGGER trg_resource_versions_local_updated_at ON public.resource_versions IS 'Cập nhật timestamp runtime khi application chưa cập nhật; không áp dụng lên master_*.';


--
-- Name: content_unit_texts content_unit_texts_content_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_unit_texts
    ADD CONSTRAINT content_unit_texts_content_unit_id_fkey FOREIGN KEY (content_unit_id) REFERENCES public.content_units(id) ON DELETE RESTRICT;


--
-- Name: content_unit_texts content_unit_texts_page_result_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_unit_texts
    ADD CONSTRAINT content_unit_texts_page_result_id_fkey FOREIGN KEY (page_result_id) REFERENCES public.ocr_page_results(id) ON DELETE RESTRICT;


--
-- Name: content_units content_units_parent_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_units
    ADD CONSTRAINT content_units_parent_unit_id_fkey FOREIGN KEY (parent_unit_id) REFERENCES public.content_units(id) ON DELETE RESTRICT;


--
-- Name: content_units content_units_resource_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_units
    ADD CONSTRAINT content_units_resource_version_id_fkey FOREIGN KEY (resource_version_id) REFERENCES public.resource_versions(id) ON DELETE RESTRICT;


--
-- Name: etl_stage_runs etl_stage_runs_resource_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.etl_stage_runs
    ADD CONSTRAINT etl_stage_runs_resource_version_id_fkey FOREIGN KEY (resource_version_id) REFERENCES public.resource_versions(id) ON DELETE RESTRICT;


--
-- Name: content_units fk_content_parent_same_version; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_units
    ADD CONSTRAINT fk_content_parent_same_version FOREIGN KEY (resource_version_id, parent_unit_id) REFERENCES public.content_units(resource_version_id, id) ON DELETE RESTRICT;


--
-- Name: ocr_jobs ocr_jobs_resource_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ocr_jobs
    ADD CONSTRAINT ocr_jobs_resource_version_id_fkey FOREIGN KEY (resource_version_id) REFERENCES public.resource_versions(id) ON DELETE RESTRICT;


--
-- Name: ocr_page_results ocr_page_results_page_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ocr_page_results
    ADD CONSTRAINT ocr_page_results_page_task_id_fkey FOREIGN KEY (page_task_id) REFERENCES public.ocr_page_tasks(id) ON DELETE RESTRICT;


--
-- Name: ocr_page_tasks ocr_page_tasks_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ocr_page_tasks
    ADD CONSTRAINT ocr_page_tasks_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.ocr_jobs(id) ON DELETE RESTRICT;


--
-- Name: resource_deletion_events resource_deletion_events_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_deletion_events
    ADD CONSTRAINT resource_deletion_events_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.master_learning_resources(id);


--
-- Name: resource_grade_levels resource_grade_levels_grade_level_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_grade_levels
    ADD CONSTRAINT resource_grade_levels_grade_level_id_fkey FOREIGN KEY (grade_level_id) REFERENCES public.master_grade_levels(id) ON DELETE RESTRICT;


--
-- Name: resource_grade_levels resource_grade_levels_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_grade_levels
    ADD CONSTRAINT resource_grade_levels_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.master_learning_resources(id) ON DELETE RESTRICT;


--
-- Name: resource_subjects resource_subjects_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_subjects
    ADD CONSTRAINT resource_subjects_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.master_learning_resources(id) ON DELETE RESTRICT;


--
-- Name: resource_subjects resource_subjects_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_subjects
    ADD CONSTRAINT resource_subjects_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id) ON DELETE RESTRICT;


--
-- Name: resource_versions resource_versions_parent_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_versions
    ADD CONSTRAINT resource_versions_parent_version_id_fkey FOREIGN KEY (parent_version_id) REFERENCES public.resource_versions(id) ON DELETE RESTRICT;


--
-- Name: resource_versions resource_versions_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_versions
    ADD CONSTRAINT resource_versions_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.master_learning_resources(id) ON DELETE RESTRICT;


--
-- Name: source_file_imports source_file_imports_processing_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_file_imports
    ADD CONSTRAINT source_file_imports_processing_job_id_fkey FOREIGN KEY (processing_job_id) REFERENCES public.ocr_jobs(id) ON DELETE RESTRICT;


--
-- Name: source_file_imports source_file_imports_target_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_file_imports
    ADD CONSTRAINT source_file_imports_target_version_id_fkey FOREIGN KEY (target_version_id) REFERENCES public.resource_versions(id) ON DELETE RESTRICT;


--
-- Name: subjects subjects_grade_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subjects
    ADD CONSTRAINT subjects_grade_id_fkey FOREIGN KEY (grade_id) REFERENCES public.master_grade_levels(id) ON DELETE RESTRICT;


--
-- Name: sync_auto_streams sync_auto_streams_config_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_auto_streams
    ADD CONSTRAINT sync_auto_streams_config_id_fkey FOREIGN KEY (config_id) REFERENCES public.sync_auto_config(id) ON DELETE CASCADE;


--
-- Name: sync_reconciliation_discrepancies sync_reconciliation_discrepancies_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_reconciliation_discrepancies
    ADD CONSTRAINT sync_reconciliation_discrepancies_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.sync_reconciliation_runs(run_id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict 9dyM8JdBsTmhcOfxY7KIIhcT1ZYrcdeS2wu9kAGDTZEu0nqeEpSrYLoJ7nvmZap


-- Stamp the verified schema revision for subsequent Alembic checks.
INSERT INTO public.alembic_version (version_num) VALUES ('0020');
