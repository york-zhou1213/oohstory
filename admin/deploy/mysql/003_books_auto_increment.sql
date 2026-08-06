ALTER TABLE library_membership_events
    DROP FOREIGN KEY fk_membership_events_book;
ALTER TABLE download_jobs
    DROP FOREIGN KEY fk_download_jobs_book;
ALTER TABLE object_assets
    DROP FOREIGN KEY fk_object_assets_book;

ALTER TABLE books
    MODIFY COLUMN id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT;

ALTER TABLE library_membership_events
    ADD CONSTRAINT fk_membership_events_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE CASCADE;
ALTER TABLE download_jobs
    ADD CONSTRAINT fk_download_jobs_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE CASCADE;
ALTER TABLE object_assets
    ADD CONSTRAINT fk_object_assets_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE CASCADE;
