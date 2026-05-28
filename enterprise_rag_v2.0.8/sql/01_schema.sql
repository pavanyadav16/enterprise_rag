-- =============================================================================
-- Enterprise RAG Chatbot — SQL Server DDL
-- =============================================================================
-- Run this script once against your SQL Server instance.
-- Database: EnterpriseRAG
-- =============================================================================

USE EnterpriseRAG;
GO

-- ---------------------------------------------------------------------------
-- Table: CIS_MAST_ROLE
-- CMR_ROLE_TYPE = 1 (default). Only roles with CMR_ROLE_TYPE = 1 are used.
-- ---------------------------------------------------------------------------
CREATE TABLE CIS_MAST_ROLE (
    CMR_ID          INT IDENTITY(1,1) PRIMARY KEY,
    CMR_ROLE_NAME   NVARCHAR(100)  NOT NULL UNIQUE,
    CMR_ROLE_TYPE   INT            NOT NULL DEFAULT 1,
    CMR_isVALID     BIT            NOT NULL DEFAULT 1,
    CMR_CREATED_ON  DATETIME2      NOT NULL DEFAULT GETDATE(),
    CMR_UPDATED_ON  DATETIME2      NOT NULL DEFAULT GETDATE()
);
GO

-- ---------------------------------------------------------------------------
-- Table: SPE_ADMIN_USER
-- SAU_LOGIN must match the JWT 'sub' claim.
-- ---------------------------------------------------------------------------
CREATE TABLE SPE_ADMIN_USER (
    SAU_ID          INT IDENTITY(1,1) PRIMARY KEY,
    SAU_LOGIN       NVARCHAR(200)  NOT NULL UNIQUE,
    SAU_NAME        NVARCHAR(200)  NOT NULL,
    SAU_EMAIL       NVARCHAR(300),
    SAU_FLAG        BIT            NOT NULL DEFAULT 1,
    SAU_CREATE_DATE DATETIME2      NOT NULL DEFAULT GETDATE(),
    SAU_MODIFY_DATE DATETIME2      NOT NULL DEFAULT GETDATE()
);
GO

-- ---------------------------------------------------------------------------
-- Table: CIS_MAP_USER_ROLE
-- Many-to-many: SPE_ADMIN_USER <-> CIS_MAST_ROLE
-- Always query with CMUR_isValid = 1.
-- ---------------------------------------------------------------------------
CREATE TABLE CIS_MAP_USER_ROLE (
    CMUR_ID         INT IDENTITY(1,1) PRIMARY KEY,
    CMUR_USER_ID    INT NOT NULL REFERENCES SPE_ADMIN_USER(SAU_ID)  ON DELETE CASCADE,
    CMUR_ROLE_ID    INT NOT NULL REFERENCES CIS_MAST_ROLE(CMR_ID)   ON DELETE CASCADE,
    CMUR_isValid    BIT       NOT NULL DEFAULT 1,
    CMUR_CREATED_ON DATETIME2 NOT NULL DEFAULT GETDATE(),
    CONSTRAINT uq_user_role UNIQUE (CMUR_USER_ID, CMUR_ROLE_ID)
);
GO

-- ---------------------------------------------------------------------------
-- Table: RAG_SOURCE_TYPES
-- Lookup for supported source categories.
-- Always query with RST_isValid = 1.
-- ---------------------------------------------------------------------------
CREATE TABLE RAG_SOURCE_TYPES (
    RST_ID          INT IDENTITY(1,1) PRIMARY KEY,
    RST_TYPE_NAME   NVARCHAR(50)  NOT NULL UNIQUE,
    RST_isValid     BIT           NOT NULL DEFAULT 1,
    RST_CREATED_ON  DATETIME2     NOT NULL DEFAULT GETDATE()
);
GO

INSERT INTO RAG_SOURCE_TYPES (RST_TYPE_NAME) VALUES
    ('pdf'), ('docx'), ('txt'), ('image'), ('excel'),
    ('database'), ('url');
GO

-- ---------------------------------------------------------------------------
-- Table: RAG_RAG_SOURCES
-- Master list of indexable content sources.
-- ---------------------------------------------------------------------------
CREATE TABLE RAG_RAG_SOURCES (
    RRS_ID              INT IDENTITY(1,1) PRIMARY KEY,
    RRS_SOURCE_NAME     NVARCHAR(300)  NOT NULL,
    RRS_TYPE_ID         INT            NOT NULL REFERENCES RAG_SOURCE_TYPES(RST_ID),
    RRS_SOURCE_PATH     NVARCHAR(2000),
    RRS_DESCRIPTION     NVARCHAR(1000),
    RRS_EXTRA_TEXT      NVARCHAR(MAX),
    RRS_isValid         BIT            NOT NULL DEFAULT 1,
    RRS_LAST_INDEXED_AT DATETIME2,
    RRS_INDEX_STATUS    NVARCHAR(50)   NOT NULL DEFAULT 'pending',
    RRS_INDEX_ERROR     NVARCHAR(2000),
    RRS_CREATED_BY      INT            REFERENCES SPE_ADMIN_USER(SAU_ID),
    RRS_CREATED_AT      DATETIME2      NOT NULL DEFAULT GETDATE(),
    RRS_UPDATED_BY      INT            REFERENCES SPE_ADMIN_USER(SAU_ID),
    RRS_UPDATED_AT      DATETIME2      NOT NULL DEFAULT GETDATE()
);
GO

-- ---------------------------------------------------------------------------
-- Table: RAG_SOURCE_DB_QUERY
-- DB-type source query config and optional role-column mapping.
-- Always query with RSDQ_isValid = 1.
-- ---------------------------------------------------------------------------
CREATE TABLE RAG_SOURCE_DB_QUERY (
    RSDQ_ID          INT IDENTITY(1,1) PRIMARY KEY,
    RSDQ_SOURCE_ID   INT            NOT NULL REFERENCES RAG_RAG_SOURCES(RRS_ID) ON DELETE CASCADE,
    RSDQ_QUERY_SQL   NVARCHAR(MAX)  NOT NULL,
    RSDQ_ROLE_COLUMN NVARCHAR(100),
    RSDQ_DESCRIPTION NVARCHAR(500),
    RSDQ_isValid     BIT            NOT NULL DEFAULT 1,
    RSDQ_CREATED_AT  DATETIME2      NOT NULL DEFAULT GETDATE(),
    RSDQ_UPDATED_AT  DATETIME2      NOT NULL DEFAULT GETDATE()
);
GO

-- ---------------------------------------------------------------------------
-- Table: RAG_SOURCE_ROLES
-- Many-to-many: RAG_RAG_SOURCES <-> CIS_MAST_ROLE
-- Always query with RSR_isValid = 1.
-- ---------------------------------------------------------------------------
CREATE TABLE RAG_SOURCE_ROLES (
    RSR_ID          INT IDENTITY(1,1) PRIMARY KEY,
    RSR_SOURCE_ID   INT NOT NULL REFERENCES RAG_RAG_SOURCES(RRS_ID)  ON DELETE CASCADE,
    RSR_ROLE_ID     INT NOT NULL REFERENCES CIS_MAST_ROLE(CMR_ID)    ON DELETE CASCADE,
    RSR_isValid     BIT       NOT NULL DEFAULT 1,
    RSR_CREATED_ON  DATETIME2 NOT NULL DEFAULT GETDATE(),
    CONSTRAINT uq_source_role UNIQUE (RSR_SOURCE_ID, RSR_ROLE_ID)
);
GO

-- ---------------------------------------------------------------------------
-- Table: RAG_CHAT_SESSIONS
-- ---------------------------------------------------------------------------
CREATE TABLE RAG_CHAT_SESSIONS (
    RCS_SESSION_ID  UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWID(),
    RCS_USER_ID     INT          NOT NULL REFERENCES SPE_ADMIN_USER(SAU_ID),
    RCS_STARTED_AT  DATETIME2    NOT NULL DEFAULT GETDATE(),
    RCS_ENDED_AT    DATETIME2,
    RCS_IP_ADDRESS  NVARCHAR(50)
);
GO

-- ---------------------------------------------------------------------------
-- Table: RAG_CHAT_MESSAGES
-- ---------------------------------------------------------------------------
CREATE TABLE RAG_CHAT_MESSAGES (
    RCM_ID          BIGINT IDENTITY(1,1) PRIMARY KEY,
    RCM_SESSION_ID  UNIQUEIDENTIFIER NOT NULL REFERENCES RAG_CHAT_SESSIONS(RCS_SESSION_ID),
    RCM_ROLE        NVARCHAR(20)     NOT NULL,
    RCM_CONTENT     NVARCHAR(MAX)    NOT NULL,
    RCM_SOURCE_IDS  NVARCHAR(500),
    RCM_CREATED_AT  DATETIME2        NOT NULL DEFAULT GETDATE()
);
GO

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX idx_rrs_type     ON RAG_RAG_SOURCES(RRS_TYPE_ID);
CREATE INDEX idx_rrs_valid    ON RAG_RAG_SOURCES(RRS_isValid);
CREATE INDEX idx_rsr_source   ON RAG_SOURCE_ROLES(RSR_SOURCE_ID);
CREATE INDEX idx_rsr_role     ON RAG_SOURCE_ROLES(RSR_ROLE_ID);
CREATE INDEX idx_cmur_user    ON CIS_MAP_USER_ROLE(CMUR_USER_ID);
CREATE INDEX idx_cmur_role    ON CIS_MAP_USER_ROLE(CMUR_ROLE_ID);
CREATE INDEX idx_rcm_session  ON RAG_CHAT_MESSAGES(RCM_SESSION_ID);
GO

-- ---------------------------------------------------------------------------
-- Seed Data
-- ---------------------------------------------------------------------------
INSERT INTO CIS_MAST_ROLE (CMR_ROLE_NAME, CMR_ROLE_TYPE) VALUES
    ('admin',   1),
    ('analyst', 1),
    ('hr',      1),
    ('general', 1);
GO

INSERT INTO SPE_ADMIN_USER (SAU_LOGIN, SAU_NAME, SAU_EMAIL) VALUES
    ('dev-user-001', 'Dev User',   'dev@example.com'),
    ('admin-001',    'Admin User', 'admin@example.com');
GO

-- admin role to admin user
INSERT INTO CIS_MAP_USER_ROLE (CMUR_USER_ID, CMUR_ROLE_ID) VALUES (2, 1);
-- general role to dev user
INSERT INTO CIS_MAP_USER_ROLE (CMUR_USER_ID, CMUR_ROLE_ID) VALUES (1, 4);
GO

PRINT 'Enterprise RAG schema created successfully.';
GO
