"""
Update rpmvercmp procedure to match RPM 4.15 behaviour

Create Date: 2019-09-24 09:20:58.503610

"""

# revision identifiers, used by Alembic.
revision = '0d2a8d58a582'
down_revision = 'a52295dc8bcc'

from alembic import op


def upgrade():
    op.execute("""
        DROP FUNCTION rpmvercmp(varchar, varchar);
        CREATE FUNCTION rpmvercmp(a character varying, b character varying) RETURNS integer
            LANGUAGE plpgsql IMMUTABLE COST 1000
            AS $$
        DECLARE
            a_segments varchar[];
            b_segments varchar[];
            a_len integer;
            b_len integer;
            a_seg varchar;
            b_seg varchar;
        BEGIN
            IF a = b THEN RETURN 0; END IF;
            a_segments := array(SELECT (regexp_matches(a, '(\\d+|[a-zA-Z]+|[~^])', 'g'))[1]);
            b_segments := array(SELECT (regexp_matches(b, '(\\d+|[a-zA-Z]+|[~^])', 'g'))[1]);
            a_len := array_length(a_segments, 1);
            b_len := array_length(b_segments, 1);
            FOR i IN 1..coalesce(least(a_len, b_len) + 1, 0) LOOP
                a_seg = a_segments[i];
                b_seg = b_segments[i];
                IF a_seg ~ '^\\d' THEN
                    IF b_seg ~ '^\\d' THEN
                        a_seg := ltrim(a_seg, '0');
                        b_seg := ltrim(b_seg, '0');
                        CASE
                            WHEN length(a_seg) > length(b_seg) THEN RETURN 1;
                            WHEN length(a_seg) < length(b_seg) THEN RETURN -1;
                            ELSE NULL; -- equality -> fallthrough to string comparison
                        END CASE;
                    ELSE
                        RETURN 1;
                    END IF;
                ELSIF b_seg ~ '^\\d' THEN
                    RETURN -1;
                ELSIF a_seg = '~' THEN
                    IF b_seg != '~' THEN
                        RETURN -1;
                    END IF;
                ELSIF b_seg = '~' THEN
                    RETURN 1;
                ELSIF a_seg = '^' THEN
                    IF b_seg != '^' THEN
                        RETURN 1;
                    END IF;
                ELSIF b_seg = '^' THEN
                    RETURN -1;
                END IF;
                IF a_seg != b_seg THEN
                    IF a_seg < b_seg THEN RETURN -1; ELSE RETURN 1; END IF;
                END IF;
            END LOOP;
            IF b_segments[a_len + 1] = '~' THEN RETURN 1; END IF;
            IF a_segments[b_len + 1] = '~' THEN RETURN -1; END IF;
            IF b_segments[a_len + 1] = '^' THEN RETURN -1; END IF;
            IF a_segments[b_len + 1] = '^' THEN RETURN 1; END IF;
            IF a_len > b_len THEN RETURN 1; END IF;
            IF a_len < b_len THEN RETURN -1; END IF;
            RETURN 0;
        END $$;
    """)


def downgrade():
    op.execute("""
        DROP FUNCTION rpmvercmp(varchar, varchar);
        CREATE FUNCTION rpmvercmp(a character varying, b character varying) RETURNS integer
            LANGUAGE plpgsql IMMUTABLE COST 1000
            AS $$
        DECLARE
            a_segments varchar[];
            b_segments varchar[];
            a_len integer;
            b_len integer;
            a_seg varchar;
            b_seg varchar;
        BEGIN
            IF a = b THEN RETURN 0; END IF;
            a_segments := array(SELECT (regexp_matches(a, '(\\d+|[a-zA-Z]+|~)', 'g'))[1]);
            b_segments := array(SELECT (regexp_matches(b, '(\\d+|[a-zA-Z]+|~)', 'g'))[1]);
            a_len := array_length(a_segments, 1);
            b_len := array_length(b_segments, 1);
            FOR i IN 1..coalesce(least(a_len, b_len) + 1, 0) LOOP
                a_seg = a_segments[i];
                b_seg = b_segments[i];
                IF a_seg ~ '^\\d' THEN
                    IF b_seg ~ '^\\d' THEN
                        a_seg := ltrim(a_seg, '0');
                        b_seg := ltrim(b_seg, '0');
                        CASE
                            WHEN length(a_seg) > length(b_seg) THEN RETURN 1;
                            WHEN length(a_seg) < length(b_seg) THEN RETURN -1;
                            ELSE NULL; -- equality -> fallthrough to string comparison
                        END CASE;
                    ELSE
                        RETURN 1;
                    END IF;
                ELSIF b_seg ~ '^\\d' THEN
                    RETURN -1;
                ELSIF a_seg = '~' THEN
                    IF b_seg != '~' THEN
                        RETURN -1;
                    END IF;
                ELSIF b_seg = '~' THEN
                    RETURN 1;
                END IF;
                IF a_seg != b_seg THEN
                    IF a_seg < b_seg THEN RETURN -1; ELSE RETURN 1; END IF;
                END IF;
            END LOOP;
            IF b_segments[a_len + 1] = '~' THEN RETURN 1; END IF;
            IF a_segments[b_len + 1] = '~' THEN RETURN -1; END IF;
            IF a_len > b_len THEN RETURN 1; END IF;
            IF a_len < b_len THEN RETURN -1; END IF;
            RETURN 0;
        END $$;
    """)
