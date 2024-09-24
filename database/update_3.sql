PRAGMA foreign_keys = off;
BEGIN TRANSACTION;
ALTER TABLE `queue` RENAME TO `queue_old`;
CREATE TABLE IF NOT EXISTS `queue` (
	`id` integer primary key NOT NULL UNIQUE,
	`olymp_id` INTEGER NOT NULL,
	`participant_id` INTEGER NOT NULL,
	`problem_id` INTEGER NOT NULL,
	`status` integer NOT NULL DEFAULT 0,
	`examiner_id` INTEGER,
	CHECK (`status` IN (0, 1) OR `examiner_id` IS NOT NULL),
	FOREIGN KEY(`olymp_id`) REFERENCES `olymps`(`id`),
	FOREIGN KEY(`status`) REFERENCES `queue_status`(`id`),
	FOREIGN KEY(`participant_id`) REFERENCES `participants`(`id`),
	FOREIGN KEY(`problem_id`) REFERENCES `problems`(`id`),
	FOREIGN KEY(`examiner_id`) REFERENCES `examiners`(`id`)
);
INSERT INTO `queue`(`id`, `olymp_id`, `participant_id`, `problem_id`, `status`, `examiner_id`) SELECT * FROM `queue_old`;
DROP TABLE `queue_old`;
COMMIT;
PRAGMA foreign_keys = on;