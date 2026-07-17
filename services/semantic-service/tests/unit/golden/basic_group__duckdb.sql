SELECT "o"."region" AS "region", sum("o"."order_total") AS "revenue" FROM {{dataset('ds_orders')}} "o" GROUP BY 1
-- params: []
