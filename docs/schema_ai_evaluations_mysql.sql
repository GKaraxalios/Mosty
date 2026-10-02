-- MySQL schema for AI evaluation / benchmark records (admin monitoring)
-- Compatible with MySQL 8.0+. Create after chat tables if using same DB.

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS `ai_evaluations` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `test_query` TEXT NOT NULL,
  `assistant_response` TEXT NOT NULL,
  `detected_intent` VARCHAR(128) NULL,
  `requested_category` VARCHAR(256) NULL,
  `returned_categories` JSON NULL,
  `recommended_products` JSON NULL,
  `recommended_skus` JSON NULL,
  `stock_validation_passed` TINYINT(1) NOT NULL DEFAULT 1,
  `duplicate_validation_passed` TINYINT(1) NOT NULL DEFAULT 1,
  `category_validation_passed` TINYINT(1) NOT NULL DEFAULT 1,
  `memory_isolation_passed` TINYINT(1) NOT NULL DEFAULT 1,
  `formatting_validation_passed` TINYINT(1) NOT NULL DEFAULT 1,
  `hallucination_validation_passed` TINYINT(1) NOT NULL DEFAULT 1,
  `intent_score` DOUBLE NULL,
  `category_score` DOUBLE NULL,
  `stock_score` DOUBLE NULL,
  `relevance_score` DOUBLE NULL,
  `styling_score` DOUBLE NULL,
  `memory_score` DOUBLE NULL,
  `format_score` DOUBLE NULL,
  `total_score` DOUBLE NULL,
  `fail_reasons` JSON NULL,
  `raw_judge_output` TEXT NULL,
  `latency_ms` INT NULL,
  `model_name` VARCHAR(128) NULL,
  `prompt_version` VARCHAR(64) NULL,
  `evaluation_status` VARCHAR(32) NOT NULL DEFAULT 'pass',
  `dataset_name` VARCHAR(128) NULL,
  PRIMARY KEY (`id`),
  KEY `ix_ai_evaluations_created_at` (`created_at`),
  KEY `ix_ai_evaluations_status` (`evaluation_status`),
  KEY `ix_ai_evaluations_total_score` (`total_score`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='AI assistant evaluation records for admin monitoring';
