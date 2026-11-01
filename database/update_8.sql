CREATE TABLE IF NOT EXISTS `conversation_states` (
	`platform` INTEGER NOT NULL,
	`peer_id` INTEGER NOT NULL,
	`state` TEXT NOT NULL,
	PRIMARY KEY (`platform`, `peer_id`)
);
