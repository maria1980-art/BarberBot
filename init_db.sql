BEGIN;

CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE locales (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL,
    horario_apertura TIME NOT NULL,
    horario_cierre TIME NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT locales_nombre_no_vacio
        CHECK (length(trim(nombre)) > 0),
    CONSTRAINT locales_horario_valido
        CHECK (horario_cierre > horario_apertura)
);

CREATE TABLE barberos (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    local_id BIGINT NOT NULL REFERENCES locales(id) ON DELETE RESTRICT,
    nombre VARCHAR(100) NOT NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT barberos_id_local_uk UNIQUE (id, local_id),
    CONSTRAINT barberos_local_nombre_uk UNIQUE (local_id, nombre),
    CONSTRAINT barberos_nombre_no_vacio
        CHECK (length(trim(nombre)) > 0)
);

CREATE TABLE servicios (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    local_id BIGINT NOT NULL REFERENCES locales(id) ON DELETE RESTRICT,
    nombre VARCHAR(100) NOT NULL,
    precio NUMERIC(10, 2) NOT NULL,
    duracion_minutos INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT servicios_id_local_uk UNIQUE (id, local_id),
    CONSTRAINT servicios_local_nombre_uk UNIQUE (local_id, nombre),
    CONSTRAINT servicios_precio_no_negativo CHECK (precio >= 0),
    CONSTRAINT servicios_duracion_positiva CHECK (duracion_minutos > 0),
    CONSTRAINT servicios_nombre_no_vacio
        CHECK (length(trim(nombre)) > 0)
);

CREATE TABLE clientes (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    whatsapp_id VARCHAR(20) NOT NULL UNIQUE,
    nombre VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT clientes_whatsapp_formato
        CHECK (whatsapp_id ~ '^[1-9][0-9]{7,14}$')
);

CREATE TABLE citas (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    local_id BIGINT NOT NULL,
    barbero_id BIGINT NOT NULL,
    cliente_id BIGINT NOT NULL REFERENCES clientes(id) ON DELETE RESTRICT,
    servicio_id BIGINT NOT NULL,
    fecha_hora TIMESTAMPTZ NOT NULL,
    fecha_fin TIMESTAMPTZ NOT NULL,
    estado VARCHAR(20) NOT NULL DEFAULT 'confirmada',
    recordatorio_enviado_at TIMESTAMPTZ,
    recordatorio_procesando_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT citas_barbero_local_fk
        FOREIGN KEY (barbero_id, local_id)
        REFERENCES barberos (id, local_id) ON DELETE RESTRICT,
    CONSTRAINT citas_servicio_local_fk
        FOREIGN KEY (servicio_id, local_id)
        REFERENCES servicios (id, local_id) ON DELETE RESTRICT,
    CONSTRAINT citas_fecha_fin_posterior
        CHECK (fecha_fin > fecha_hora),
    CONSTRAINT citas_estado_valido
        CHECK (estado IN (
            'pendiente',
            'confirmada',
            'cancelada',
            'completada',
            'no_presentado'
        )),
    CONSTRAINT citas_sin_solapamiento
        EXCLUDE USING GIST (
            barbero_id WITH =,
            tstzrange(fecha_hora, fecha_fin, '[)') WITH &&
        )
        WHERE (estado IN ('pendiente', 'confirmada'))
);

ALTER TABLE citas
    ADD COLUMN IF NOT EXISTS recordatorio_enviado_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS recordatorio_procesando_at TIMESTAMPTZ;

CREATE INDEX citas_local_fecha_idx
    ON citas (local_id, fecha_hora);
CREATE INDEX citas_barbero_fecha_idx
    ON citas (barbero_id, fecha_hora);
CREATE INDEX citas_cliente_fecha_idx
    ON citas (cliente_id, fecha_hora DESC);
CREATE INDEX citas_recordatorios_pendientes_idx
    ON citas (fecha_hora)
    WHERE estado = 'confirmada' AND recordatorio_enviado_at IS NULL;

CREATE TABLE historial_conversaciones (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    whatsapp_id VARCHAR(20) NOT NULL,
    rol VARCHAR(20) NOT NULL,
    contenido TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT historial_rol_valido
        CHECK (rol IN ('user', 'assistant'))
);

CREATE INDEX historial_whatsapp_fecha_idx
    ON historial_conversaciones (whatsapp_id, created_at DESC);

DO $seed$
DECLARE
    demo_local_id BIGINT;
BEGIN
    INSERT INTO locales (nombre, horario_apertura, horario_cierre)
    VALUES (
        'Barbería Demo Sigüeiro',
        TIME '09:00',
        TIME '19:00'
    )
    RETURNING id INTO demo_local_id;

    INSERT INTO barberos (local_id, nombre)
    VALUES
        (demo_local_id, 'Alex García'),
        (demo_local_id, 'Noa Fernández'),
        (demo_local_id, 'Dani Rodríguez');

    INSERT INTO servicios (local_id, nombre, precio, duracion_minutos)
    VALUES
        (demo_local_id, 'Corte clásico', 15.00, 30),
        (demo_local_id, 'Arreglo de barba', 12.00, 30),
        (demo_local_id, 'Corte y peinado', 28.00, 60),
        (demo_local_id, 'Coloración', 60.00, 90),
        (demo_local_id, 'Tratamiento completo', 75.00, 120);
END;
$seed$;

COMMIT;
