-- anx_device_plugin/create_db.sql
-- SQL script to create the database7.db and tb_books table

CREATE TABLE IF NOT EXISTS tb_books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    cover_path TEXT,
    file_path TEXT NOT NULL UNIQUE,
    author TEXT,
    create_time TEXT,
    update_time TEXT,
    file_md5 TEXT UNIQUE,
    last_read_position TEXT,
    reading_percentage REAL,
    is_deleted INTEGER DEFAULT 0,
    rating REAL,
    group_id INTEGER,
    description TEXT
);

-- Example of inserting a dummy book
-- INSERT INTO tb_books (title, cover_path, file_path, author, create_time, update_time, file_md5, last_read_position, reading_percentage, is_deleted, rating, group_id, description)
-- VALUES (
--     'Example Book',
--     'cover/Example Book.jpg',
--     'file/Example Book - Unknown Author.epub',
--     'Unknown Author',
--     '2024-07-19T10:00:00.000Z',
--     '2024-07-19T10:00:00.000Z',
--     'abcdef1234567890abcdef1234567890',
--     '',
--     0.0,
--     0,
--     0.0,
--     0,
--     'This is an example book.'
-- );

-- SQL script to create the tb_reading_time table
CREATE TABLE tb_reading_time  (
    id INTEGER PRIMARY KEY,
    book_id INTEGER,
    date TEXT,
    reading_time INTEGER
)