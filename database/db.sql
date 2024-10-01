CREATE TABLE IF NOT EXISTS `olymp_status` (
	`id` integer primary key NOT NULL UNIQUE,
	`name` text NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS `queue_status` (
	`id` integer primary key NOT NULL UNIQUE,
	`name` text NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS `block_types` (
	`id` integer primary key NOT NULL UNIQUE,
	`name` text NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS `olymps` (
	`id` integer primary key NOT NULL UNIQUE,
	`name` text NOT NULL UNIQUE,
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
	`olymp_id` INTEGER NOT NULL,
	`name` TEXT NOT NULL,
	FOREIGN KEY(`olymp_id`) REFERENCES `olymps`(`id`),
	UNIQUE (`olymp_id`, `name`)
);
CREATE TABLE IF NOT EXISTS `problem_blocks` (
	`id` integer primary key NOT NULL UNIQUE,
	`olymp_id` INTEGER NOT NULL,
	`block_type` INTEGER,
	`path` TEXT,
	`first_problem` INTEGER NOT NULL,
	`second_problem` INTEGER NOT NULL,
	`third_problem` INTEGER NOT NULL,
	FOREIGN KEY(`olymp_id`) REFERENCES `olymps`(`id`),
	FOREIGN KEY(`first_problem`) REFERENCES `problems`(`id`),
	FOREIGN KEY(`second_problem`) REFERENCES `problems`(`id`),
	FOREIGN KEY(`third_problem`) REFERENCES `problems`(`id`),
	FOREIGN KEY(`block_type`) REFERENCES `block_types`(`id`),
	UNIQUE (`olymp_id`, `block_type`),
	CHECK (`first_problem` != `second_problem` AND `second_problem` != `third_problem` and `third_problem` != `first_problem`)
);
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
CREATE TABLE IF NOT EXISTS `examiner_problems` (
	`examiner_id` integer NOT NULL,
	`problem_id` integer NOT NULL,
	FOREIGN KEY(`examiner_id`) REFERENCES `examiners`(`id`),
	FOREIGN KEY(`problem_id`) REFERENCES `problems`(`id`)
);
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