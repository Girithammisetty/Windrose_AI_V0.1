SELECT "o"."region" AS "region", arg_min("o"."status", "o"."order_id") AS "first_status", count(DISTINCT "o"."region") AS "region_count" FROM {{dataset('ds_orders')}} "o" GROUP BY 1
-- params: []
