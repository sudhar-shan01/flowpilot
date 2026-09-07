CREATE TABLE IF NOT EXISTS leads (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(320) NOT NULL,
    company VARCHAR(200) NOT NULL,
    message TEXT NOT NULL,
    budget NUMERIC(14, 2) NOT NULL
        CHECK (budget >= 0 AND budget <= 1000000000),
    category VARCHAR(80) NOT NULL,
    priority VARCHAR(6) NOT NULL
        CHECK (priority IN ('high', 'medium', 'low')),
    lead_score SMALLINT NOT NULL
        CHECK (lead_score BETWEEN 0 AND 100),
    short_summary VARCHAR(500) NOT NULL,
    recommended_action VARCHAR(500) NOT NULL
);
