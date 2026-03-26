-- PostGIS + Kullanıcı İzleri (tasks 1.2)
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS user_traces (
    id SERIAL PRIMARY KEY,
    geom geometry(Point, 4326) NOT NULL,
    tag_type VARCHAR(32) NOT NULL CHECK (tag_type IN ('Güvenli', 'Az Işıklı', 'Issız')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_fingerprint VARCHAR(64)
);

CREATE INDEX IF NOT EXISTS idx_user_traces_geom ON user_traces USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_user_traces_created_at ON user_traces (created_at DESC);
