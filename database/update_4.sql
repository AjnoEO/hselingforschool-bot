PRAGMA foreign_keys = off;
BEGIN TRANSACTION;
ALTER TABLE `participants` RENAME TO `participants_old`;
CREATE TABLE IF NOT EXISTS `participants` (
	`id` integer primary key NOT NULL UNIQUE,
	`olymp_id` INTEGER NOT NULL,
	`user_id` INTEGER NOT NULL,
	`grade` INTEGER NOT NULL,
	`last_block_number` INTEGER NOT NULL DEFAULT 1 CHECK (`last_block_number` BETWEEN 1 AND 3),
	FOREIGN KEY(`olymp_id`) REFERENCES `olymps`(`id`),
	FOREIGN KEY(`user_id`) REFERENCES `users`(`user_id`),
	UNIQUE(`olymp_id`, `user_id`)
);
INSERT INTO `participants`(`id`, `olymp_id`, `user_id`, `grade`, `last_block_number`) SELECT * FROM `participants_old`;
DROP TABLE `participants_old`;
ALTER TABLE `examiners` RENAME TO `examiners_old`;
CREATE TABLE IF NOT EXISTS `examiners` (
	`id` integer primary key NOT NULL UNIQUE,
	`olymp_id` INTEGER NOT NULL,
	`user_id` INTEGER NOT NULL,
	`conference_link` TEXT NOT NULL,
	`busyness_level` INTEGER NOT NULL,
	`is_busy` INTEGER NOT NULL CHECK (`is_busy` IN (0, 1)),
	FOREIGN KEY(`olymp_id`) REFERENCES `olymps`(`id`),
	FOREIGN KEY(`user_id`) REFERENCES `users`(`user_id`),
	UNIQUE(`olymp_id`, `user_id`)
);
INSERT INTO `examiners`(`id`, `olymp_id`, `user_id`, `conference_link`, `busyness_level`, `is_busy`) SELECT * FROM `examiners_old`;
DROP TABLE `examiners_old`;
COMMIT;
PRAGMA foreign_keys = on;