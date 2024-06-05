"""
Optimize rpmvercmp a bit

Create Date: 2017-02-24 17:46:01.529567

"""

# revision identifiers, used by Alembic.
revision = '2a0e9d5529c9'
down_revision = '96210b1e8db3'

from alembic import op


def upgrade():
    op.execute("""
CREATE OR REPLACE FUNCTION rpmvercmp(a varchar, b varchar)
    RETURNS integer AS $$
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
END $$ LANGUAGE plpgsql IMMUTABLE COST 1000;

CREATE OR REPLACE FUNCTION rpmvercmp_evr(epoch1 integer, version1 varchar,release1 varchar,
                                         epoch2 integer, version2 varchar,release2 varchar)
    RETURNS integer AS $$
DECLARE
    vercmp_result integer;
BEGIN
    epoch1 := COALESCE(epoch1, 0);
    epoch2 := COALESCE(epoch2, 0);
    IF epoch1 < epoch2 THEN RETURN -1; END IF;
    IF epoch1 > epoch2 THEN RETURN 1; END IF;
    vercmp_result := rpmvercmp(version1, version2);
    IF vercmp_result != 0 THEN RETURN vercmp_result; END IF;
    RETURN rpmvercmp(release1, release2);
END $$ LANGUAGE plpgsql IMMUTABLE COST 1500;
    """)


def downgrade():
    op.execute("""
CREATE OR REPLACE FUNCTION rpmvercmp(a varchar, b varchar)
    RETURNS integer AS $$
DECLARE
    a_segments varchar[] = array(SELECT (regexp_matches(a, '(\\d+|[a-zA-Z]+|~)', 'g'))[1]);
    b_segments varchar[] = array(SELECT (regexp_matches(b, '(\\d+|[a-zA-Z]+|~)', 'g'))[1]);
    a_len integer = array_length(a_segments, 1);
    b_len integer = array_length(b_segments, 1);
    a_seg varchar;
    b_seg varchar;
BEGIN
    FOR i IN 1..coalesce(least(a_len, b_len) + 1, 0) LOOP
        a_seg = a_segments[i];
        b_seg = b_segments[i];
        IF a_seg = '~' THEN
            IF b_seg != '~' THEN
                RETURN -1;
            END IF;
        ELSIF b_seg = '~' THEN
            RETURN 1;
        END IF;
        IF a_seg ~ '^\\d' THEN
            IF b_seg ~ '^\\d' THEN
                a_seg = ltrim(a_seg, '0');
                b_seg = ltrim(b_seg, '0');
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
END $$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION rpmvercmp_evr(epoch1 integer, version1 varchar,release1 varchar,
                                         epoch2 integer, version2 varchar,release2 varchar)
    RETURNS integer AS $$
DECLARE
    vercmp_result integer;
BEGIN
    epoch1 := COALESCE(epoch1, 0);
    epoch2 := COALESCE(epoch2, 0);
    IF epoch1 < epoch2 THEN RETURN -1; END IF;
    IF epoch1 > epoch2 THEN RETURN 1; END IF;
    vercmp_result := rpmvercmp(version1, version2);
    IF vercmp_result != 0 THEN RETURN vercmp_result; END IF;
    RETURN rpmvercmp(release1, release2);
END $$ LANGUAGE plpgsql IMMUTABLE;
    """)
