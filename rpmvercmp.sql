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
