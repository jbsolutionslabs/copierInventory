-- =============================================================================
-- Migration 002: Add serial column to watchlist
-- Target:        PostgreSQL 14+ (Railway)
-- Safe to re-run: uses IF NOT EXISTS
-- =============================================================================

ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS serial VARCHAR(100);
