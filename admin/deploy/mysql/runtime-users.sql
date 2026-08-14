-- Fresh-install runtime accounts for MySQL 8.0.18+.
--
-- Run this only after deploy/mysql/init.sql. MySQL generates both passwords;
-- capture the client output in a root-only file, install the two values in the
-- corresponding service password files, then remove the capture file.
-- No reusable or default password is stored in this repository.

CREATE USER IF NOT EXISTS 'oohstory_library_writer'@'127.0.0.1'
    IDENTIFIED BY RANDOM PASSWORD;
CREATE USER IF NOT EXISTS 'oohstory_library_reader'@'127.0.0.1'
    IDENTIFIED BY RANDOM PASSWORD;

GRANT 'oohstory_library_writer_role'@'%'
    TO 'oohstory_library_writer'@'127.0.0.1';
GRANT 'oohstory_library_reader_role'@'%'
    TO 'oohstory_library_reader'@'127.0.0.1';

SET DEFAULT ROLE 'oohstory_library_writer_role'@'%'
    TO 'oohstory_library_writer'@'127.0.0.1';
SET DEFAULT ROLE 'oohstory_library_reader_role'@'%'
    TO 'oohstory_library_reader'@'127.0.0.1';
