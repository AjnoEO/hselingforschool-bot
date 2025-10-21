PRAGMA writable_schema = ON;

UPDATE sqlite_schema 
SET sql = REPLACE(sql, 'REFERENCES "examiners_old"', 'REFERENCES "examiners"')
WHERE name = 'examiner_problems'
AND type = 'table';

UPDATE sqlite_schema 
SET sql = REPLACE(
    REPLACE(sql, 'REFERENCES "participants_old"', 'REFERENCES "participants"'),
    'REFERENCES "examiners_old"', 'REFERENCES "examiners"'
)
WHERE name = 'queue'
AND type = 'table';

PRAGMA writable_schema = OFF;