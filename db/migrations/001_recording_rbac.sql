-- Migration 001: Recording RBAC tables
-- Database: ifs (PostgreSQL, localhost:5432, user=ifs)
-- Run: psql -h localhost -U ifs -d ifs -f db/migrations/001_recording_rbac.sql
--
-- New tables for atp-dev-24 recording server access control.
-- These are not present in atp-dev (Ubuntu 14 version).

BEGIN;

-- Auditor accounts (users who can listen to recordings)
CREATE TABLE IF NOT EXISTS amp_auditors (
    id         SERIAL PRIMARY KEY,
    username   VARCHAR(64) UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Line descriptions (DN → human-readable name)
CREATE TABLE IF NOT EXISTS lines (
    id          SERIAL PRIMARY KEY,
    dn          VARCHAR(32) UNIQUE NOT NULL,
    description TEXT,
    updated_at  TIMESTAMP DEFAULT NOW()
);

-- Which auditors can access which lines (many-to-many)
CREATE TABLE IF NOT EXISTS auditor_lines (
    auditor_id INTEGER REFERENCES amp_auditors(id) ON DELETE CASCADE,
    line_id    INTEGER REFERENCES lines(id) ON DELETE CASCADE,
    PRIMARY KEY (auditor_id, line_id)
);

COMMIT;
