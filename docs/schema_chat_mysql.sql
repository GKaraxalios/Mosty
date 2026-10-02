-- MySQL schema for chat sessions and messages (MostyAPI conversation storage)
-- Run this once to create tables. Compatible with MySQL 8.0+ (JSON, DATETIME(6)).

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ---------------------------------------------------------------------------
-- chat_sessions
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS `chat_messages`;
DROP TABLE IF EXISTS `chat_sessions`;

CREATE TABLE `chat_sessions` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `session_id` VARCHAR(64) NOT NULL COMMENT 'Business id (e.g. 16 hex chars), unique per conversation',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  `language` VARCHAR(16) NOT NULL DEFAULT 'auto',
  `user_preferences` JSON NULL COMMENT 'Budget, preferred_colors, preferred_categories, etc.',
  `retrieved_skus` JSON NULL COMMENT 'List of SKUs already suggested in this session',
  `last_query` TEXT NULL,
  `last_answer` TEXT NULL,
  `deleted_at` DATETIME(6) NULL DEFAULT NULL COMMENT 'Soft delete; NULL = active',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_chat_sessions_session_id` (`session_id`),
  KEY `ix_chat_sessions_updated_at` (`updated_at`),
  KEY `ix_chat_sessions_deleted_at` (`deleted_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Chat conversation sessions';

-- ---------------------------------------------------------------------------
-- chat_messages
-- ---------------------------------------------------------------------------
CREATE TABLE `chat_messages` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `session_id` VARCHAR(64) NOT NULL COMMENT 'References chat_sessions.session_id',
  `role` VARCHAR(32) NOT NULL COMMENT 'user | assistant | system | tool',
  `content` TEXT NOT NULL,
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `sort_order` INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Order within session (0,1,2,...)',
  PRIMARY KEY (`id`),
  KEY `ix_chat_messages_session_order` (`session_id`, `sort_order`),
  CONSTRAINT `fk_chat_messages_session` FOREIGN KEY (`session_id`) REFERENCES `chat_sessions` (`session_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Messages (turns) per chat session';

SET FOREIGN_KEY_CHECKS = 1;
