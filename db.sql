CREATE TABLE IF NOT EXISTS `olymp_status` (
	`id` integer primary key NOT NULL UNIQUE,
	`name` text NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS `queue_status` (
	`id` integer primary key NOT NULL UNIQUE,
	`name` text NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS `olymps` (
	`id` integer primary key NOT NULL UNIQUE,
	`year` integer NOT NULL,
	`name` text NOT NULL,
	`status` integer NOT NULL DEFAULT 0,
	FOREIGN KEY(`status`) REFERENCES `olymp_status`(`id`)
);
CREATE TABLE IF NOT EXISTS `users` (
	`user_id` integer primary key NOT NULL UNIQUE,
	`tg_id` INTEGER UNIQUE,
	`tg_handle` TEXT NOT NULL UNIQUE,
	`name` TEXT NOT NULL,
	`surname` TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS `problems` (
	`id` integer primary key NOT NULL UNIQUE,
	`olymps_id` INTEGER NOT NULL,
	`junior_no` INTEGER,
	`senior_no` INTEGER,
	`name` TEXT NOT NULL,
	FOREIGN KEY(`olymps_id`) REFERENCES `olymps`(`id`)
);
CREATE TABLE IF NOT EXISTS `participants` (
	`id` integer primary key NOT NULL UNIQUE,
	`olymp_id` INTEGER NOT NULL,
	`user_id` INTEGER NOT NULL,
	`grade` INTEGER NOT NULL,
	FOREIGN KEY(`olymp_id`) REFERENCES `olymps`(`id`),
	FOREIGN KEY(`user_id`) REFERENCES `users`(`user_id`)
);
CREATE TABLE IF NOT EXISTS `examiners` (
	`id` integer primary key NOT NULL UNIQUE,
	`olymp_id` INTEGER NOT NULL,
	`user_id` INTEGER NOT NULL,
	`problems` TEXT NOT NULL,
	`conference_link` TEXT NOT NULL,
	`busyness_level` INTEGER NOT NULL,
	`is_busy` INTEGER NOT NULL CHECK (`is_busy` IN (0, 1)),
	FOREIGN KEY(`olymp_id`) REFERENCES `olymps`(`id`),
	FOREIGN KEY(`user_id`) REFERENCES `users`(`user_id`)
);
CREATE TABLE IF NOT EXISTS `queue` (
	`pos` integer primary key NOT NULL UNIQUE,
	`olymp_id` INTEGER NOT NULL,
	`participant_id` INTEGER NOT NULL,
	`problem_id` INTEGER NOT NULL,
	`status` integer NOT NULL,
	`examiner_id` INTEGER,
	CHECK (`status` IN (0, 1) OR `examiner_id` IS NOT NULL),
	FOREIGN KEY(`olymp_id`) REFERENCES `olymps`(`id`),
	FOREIGN KEY(`status`) REFERENCES `queue_status`(`id`),
	FOREIGN KEY(`participant_id`) REFERENCES `participants`(`id`),
	FOREIGN KEY(`problem_id`) REFERENCES `problems`(`id`),
	FOREIGN KEY(`examiner_id`) REFERENCES `examiners`(`id`)
);