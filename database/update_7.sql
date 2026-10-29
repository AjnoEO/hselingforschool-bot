PRAGMA foreign_keys = off;
PRAGMA legacy_alter_table = on;
BEGIN TRANSACTION;
-- Enum платформа (мессенджер)
CREATE TABLE IF NOT EXISTS `platforms` (
	`id` integer primary key NOT NULL UNIQUE,
	`name` text NOT NULL UNIQUE
);

-- Обновление таблицы пользователей
ALTER TABLE `users` RENAME TO `users_old`;
CREATE TABLE IF NOT EXISTS `users` (
	`user_id` integer primary key NOT NULL UNIQUE,
	`platform` INTEGER NOT NULL,
	`external_id` INTEGER,
	`handle` TEXT NOT NULL,
	`name` TEXT NOT NULL,
	`surname` TEXT NOT NULL,
	UNIQUE (`platform`, `external_id`),
	UNIQUE (`platform`, `handle`),
	FOREIGN KEY(`platform`) REFERENCES `platforms`(`id`)
);
INSERT INTO `users` (`user_id`, `platform`, `external_id`, `handle`, `name`, `surname`)
SELECT `user_id`, 0, `tg_id`, `tg_handle`, `name`, `surname` FROM `users_old`;
DROP TABLE `users_old`;

-- Исправление внешнего ключа user_tags
ALTER TABLE `user_tags` RENAME TO `user_tags_old`;
CREATE TABLE IF NOT EXISTS `user_tags` (
    `user_id` integer NOT NULL,
    `tag_id` integer NOT NULL,
	FOREIGN KEY(`user_id`) REFERENCES `users`(`user_id`) ON DELETE CASCADE,
	FOREIGN KEY(`tag_id`) REFERENCES `tags`(`id`) ON DELETE CASCADE
);
INSERT INTO `user_tags` (`user_id`, `tag_id`) SELECT `user_id`, `tag_id` FROM `user_tags_old`;
DROP TABLE `user_tags_old`;
COMMIT;
PRAGMA legacy_alter_table = off;
PRAGMA foreign_keys = on;
