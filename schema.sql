PRAGMA foreign_keys = ON;

-- ----------------------------------------------------------------------
-- Table : users
-- Gère l'authentification et l'identité des utilisateurs.
-- Le téléphone est automatiquement masqué via une colonne générée.
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK(role IN ('customer', 'provider', 'admin')),
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    phone TEXT NOT NULL,
    phone_masked TEXT GENERATED ALWAYS AS (
        substr(phone, 1, 2) || ' ** ** ** ' || substr(phone, -2)
    ) STORED,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ----------------------------------------------------------------------
-- Table : provider_profiles
-- Informations détaillées pour les prestataires de ménage.
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS provider_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE,
    bio TEXT,
    hourly_rate REAL NOT NULL CHECK(hourly_rate > 0),
    experience_years INTEGER NOT NULL DEFAULT 0 CHECK(experience_years >= 0),
    service_radius_km REAL NOT NULL DEFAULT 10.0 CHECK(service_radius_km > 0),
    latitude REAL,
    longitude REAL,
    rating_avg REAL NOT NULL DEFAULT 0.0 CHECK(rating_avg >= 0.0 AND rating_avg <= 5.0),
    reviews_count INTEGER NOT NULL DEFAULT 0 CHECK(reviews_count >= 0),
    is_verified INTEGER NOT NULL DEFAULT 0 CHECK(is_verified IN (0, 1)),
    is_available INTEGER NOT NULL DEFAULT 1 CHECK(is_available IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------
-- Table : requests
-- Demandes de prestations déposées par les clients.
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    service_date TEXT NOT NULL,
    duration_hours REAL NOT NULL CHECK(duration_hours > 0),
    surface_m2 REAL CHECK(surface_m2 > 0),
    address TEXT NOT NULL,
    postal_code TEXT NOT NULL,
    city TEXT NOT NULL,
    latitude REAL,
    longitude REAL,
    max_hourly_rate REAL CHECK(max_hourly_rate IS NULL OR max_hourly_rate > 0),
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'matching', 'matched', 'assigned', 'confirmed', 'completed', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (customer_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------
-- Table : matches
-- Résultats du matching entre une demande client et un prestataire.
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    provider_id INTEGER NOT NULL,
    match_score REAL NOT NULL CHECK(match_score >= 0.0 AND match_score <= 100.0),
    status TEXT NOT NULL DEFAULT 'suggested' CHECK(status IN ('suggested', 'accepted', 'declined', 'accepted_by_provider', 'accepted_by_customer', 'assigned', 'confirmed', 'expired')),
    matched_at TEXT NOT NULL DEFAULT (datetime('now')),
    responded_at TEXT,
    FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE CASCADE,
    FOREIGN KEY (provider_id) REFERENCES provider_profiles(id) ON DELETE CASCADE,
    UNIQUE (request_id, provider_id)
);

-- ----------------------------------------------------------------------
-- Index d'optimisation
-- ----------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

CREATE INDEX IF NOT EXISTS idx_provider_profiles_user_id ON provider_profiles(user_id);
CREATE INDEX IF NOT EXISTS idx_provider_profiles_availability ON provider_profiles(is_available, rating_avg);

CREATE INDEX IF NOT EXISTS idx_requests_customer_id ON requests(customer_id);
CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
CREATE INDEX IF NOT EXISTS idx_requests_service_date ON requests(service_date);

CREATE INDEX IF NOT EXISTS idx_matches_request_id ON matches(request_id);
CREATE INDEX IF NOT EXISTS idx_matches_provider_id ON matches(provider_id);
CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status);
CREATE INDEX IF NOT EXISTS idx_matches_score ON matches(match_score DESC);

-- ----------------------------------------------------------------------
-- Table : conversations
-- Salons de messagerie anonymisée liés à un match validé/proposé.
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL UNIQUE,
    customer_id INTEGER NOT NULL,
    provider_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE,
    FOREIGN KEY (customer_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (provider_id) REFERENCES provider_profiles(id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------
-- Table : messages
-- Messages échangés de manière anonyme dans une conversation.
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    sender_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    sent_at TEXT NOT NULL DEFAULT (datetime('now')),
    is_flagged INTEGER NOT NULL DEFAULT 0,
    moderation_reasons TEXT,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conversations_match_id ON conversations(match_id);
CREATE INDEX IF NOT EXISTS idx_conversations_customer_id ON conversations(customer_id);
CREATE INDEX IF NOT EXISTS idx_conversations_provider_id ON conversations(provider_id);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_sent_at ON messages(sent_at);
CREATE INDEX IF NOT EXISTS idx_messages_is_flagged ON messages(is_flagged);

-- ----------------------------------------------------------------------
-- Table : provider_slots (Agenda & Créneaux de disponibilité)
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS provider_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER NOT NULL,
    date TEXT NOT NULL,          -- Format 'YYYY-MM-DD'
    start_time TEXT NOT NULL,    -- Format 'HH:MM'
    end_time TEXT NOT NULL,      -- Format 'HH:MM'
    is_booked BOOLEAN DEFAULT 0,
    FOREIGN KEY (provider_id) REFERENCES provider_profiles (id)
);

CREATE INDEX IF NOT EXISTS idx_slots_provider_date ON provider_slots(provider_id, date);
CREATE INDEX IF NOT EXISTS idx_slots_booked ON provider_slots(is_booked);

-- ----------------------------------------------------------------------
-- Table : missions (Missions et réservations directes via créneaux)
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS missions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER NOT NULL,
    customer_email TEXT NOT NULL,
    date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    FOREIGN KEY (provider_id) REFERENCES provider_profiles (id)
);

CREATE INDEX IF NOT EXISTS idx_missions_provider ON missions(provider_id);
CREATE INDEX IF NOT EXISTS idx_missions_status ON missions(status);



