'''
Database URL for PostgreSQL
psql postgresql://db_user:eoOw0MT2IcJuq62UV82adBeYdeadGVQ3@dpg-d1tc4k8dl3ps739ajc50-a.singapore-postgres.render.com/dreamersunited_db
\l # List all databases
\c dreamersunited_db # Connect to the database
\dt # List all tables in the current database
\di # List all indexes in the current database
DROP DATABASE dreamersunited_db;
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = 'dreamersunited_db'
  AND pid <> pg_backend_pid();
CREATE DATABASE dreamersunited_db;
'''