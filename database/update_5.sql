CREATE TABLE IF NOT EXISTS `tags` (
    `id` integer primary key NOT NULL UNIQUE,
    `name` text NOT NULL,
    `description` text NOT NULL
);
CREATE TABLE IF NOT EXISTS `user_tags` (
    `user_id` integer NOT NULL,
    `tag_id` integer NOT NULL,
	FOREIGN KEY(`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE,
	FOREIGN KEY(`tag_id`) REFERENCES `tags`(`id`) ON DELETE CASCADE
);
ALTER TABLE `participants`
ADD COLUMN `finished` INTEGER NOT NULL CHECK (`finished` IN (0, 1)) DEFAULT 0;