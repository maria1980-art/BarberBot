BEGIN;

ALTER TABLE citas
    ADD COLUMN IF NOT EXISTS recordatorio_enviado_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS recordatorio_procesando_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS citas_recordatorios_pendientes_idx
    ON citas (fecha_hora)
    WHERE estado = 'confirmada' AND recordatorio_enviado_at IS NULL;

COMMIT;
