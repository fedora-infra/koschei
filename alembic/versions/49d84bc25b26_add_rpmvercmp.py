"""
Add rpmvercmp

Create Date: 2016-10-03 13:29:12.574775

"""

# revision identifiers, used by Alembic.
revision = '49d84bc25b26'
down_revision = '154f49b41d6a'

from alembic import op


def upgrade():
    op.execute("""
CREATE INDEX ix_applied_change_dep_name on applied_change(dep_name);

-- from rpmvercmp.sql
CREATE OR REPLACE FUNCTION rpmvercmp(a varchar, b varchar)
    RETURNS integer AS $$
DECLARE
    a_segments varchar[] = array(SELECT (regexp_matches(a, '(\d+|[a-zA-Z]+|~)', 'g'))[1]);
    b_segments varchar[] = array(SELECT (regexp_matches(b, '(\d+|[a-zA-Z]+|~)', 'g'))[1]);
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
        IF a_seg ~ '^\d' THEN
            IF b_seg ~ '^\d' THEN
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
        ELSIF b_seg ~ '^\d' THEN
            RETURN -1;
        END IF;
        IF a_seg != b_seg THEN
            IF a_seg < b_seg THEN RETURN -1; ELSE RETURN 1; END IF;
        END IF;
    END LOOP;
    CASE
        WHEN b_segments[a_len + 1] = '~' THEN RETURN 1;
        WHEN a_segments[b_len + 1] = '~' THEN RETURN -1;
        WHEN a_len > b_len THEN RETURN 1;
        WHEN a_len < b_len THEN RETURN -1;
        ELSE RETURN 0;
    END CASE;
END $$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION rpmlt(a varchar, b varchar)
    RETURNS boolean AS $$
BEGIN
    RETURN rpmvercmp(a, b) < 0;
END $$ LANGUAGE plpgsql IMMUTABLE;

CREATE OPERATOR <# (PROCEDURE=rpmlt, LEFTARG=varchar, RIGHTARG=varchar,
                    COMMUTATOR=">#", NEGATOR=">=#", RESTRICT=scalarltsel,
                    JOIN=scalarltjoinsel);

CREATE OR REPLACE FUNCTION rpmgt(a varchar, b varchar)
    RETURNS boolean AS $$
BEGIN
    RETURN rpmvercmp(a, b) > 0;
END $$ LANGUAGE plpgsql IMMUTABLE;

CREATE OPERATOR ># (PROCEDURE=rpmgt, LEFTARG=varchar, RIGHTARG=varchar,
                    COMMUTATOR="<#", NEGATOR="<=#", RESTRICT=scalargtsel,
                    JOIN=scalargtjoinsel);

CREATE OR REPLACE FUNCTION rpmle(a varchar, b varchar)
    RETURNS boolean AS $$
BEGIN
    RETURN rpmvercmp(a, b) <= 0;
END $$ LANGUAGE plpgsql IMMUTABLE;

CREATE OPERATOR <=# (PROCEDURE=rpmle, LEFTARG=varchar, RIGHTARG=varchar,
                    COMMUTATOR=">=#", NEGATOR=">#", RESTRICT=scalarltsel,
                    JOIN=scalarltjoinsel);

CREATE OR REPLACE FUNCTION rpmge(a varchar, b varchar)
    RETURNS boolean AS $$
BEGIN
    RETURN rpmvercmp(a, b) >= 0;
END $$ LANGUAGE plpgsql IMMUTABLE;

CREATE OPERATOR >=# (PROCEDURE=rpmge, LEFTARG=varchar, RIGHTARG=varchar,
                    COMMUTATOR="<=#", NEGATOR="<#", RESTRICT=scalargtsel,
                    JOIN=scalargtjoinsel);

CREATE OR REPLACE FUNCTION rpmeq(a varchar, b varchar)
    RETURNS boolean AS $$
BEGIN
    RETURN rpmvercmp(a, b) = 0;
END $$ LANGUAGE plpgsql IMMUTABLE;

CREATE OPERATOR =# (PROCEDURE=rpmeq, LEFTARG=varchar, RIGHTARG=varchar,
                    COMMUTATOR="=#", NEGATOR="!=#", RESTRICT=eqsel,
                    JOIN=eqjoinsel);

CREATE OR REPLACE FUNCTION rpmne(a varchar, b varchar)
    RETURNS boolean AS $$
BEGIN
    RETURN rpmvercmp(a, b) != 0;
END $$ LANGUAGE plpgsql IMMUTABLE;

CREATE OPERATOR !=# (PROCEDURE=rpmne, LEFTARG=varchar, RIGHTARG=varchar,
                    COMMUTATOR="!=#", NEGATOR="=#", RESTRICT=neqsel,
                    JOIN=neqjoinsel);

CREATE OPERATOR CLASS rpmcmp_ops FOR TYPE varchar USING btree AS
    OPERATOR        1       <#,
    OPERATOR        2       <=#,
    OPERATOR        3       =#,
    OPERATOR        4       >=#,
    OPERATOR        5       >#,
    FUNCTION        1       rpmvercmp(varchar, varchar);
    """)


def downgrade():
    op.execute("""
    DROP INDEX ix_applied_change_dep_name;

    DROP OPERATOR CLASS rpmcmp_ops USING btree;
    DROP OPERATOR <# (varchar, varchar);
    DROP OPERATOR <=# (varchar, varchar);
    DROP OPERATOR ># (varchar, varchar);
    DROP OPERATOR >=# (varchar, varchar);
    DROP OPERATOR =# (varchar, varchar);
    DROP OPERATOR !=# (varchar, varchar);

    DROP FUNCTION rpmlt(varchar, varchar);
    DROP FUNCTION rpmgt(varchar, varchar);
    DROP FUNCTION rpmle(varchar, varchar);
    DROP FUNCTION rpmge(varchar, varchar);
    DROP FUNCTION rpmeq(varchar, varchar);
    DROP FUNCTION rpmne(varchar, varchar);
    DROP FUNCTION rpmvercmp(varchar, varchar);
    """)
