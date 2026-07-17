SELECT "c"."tier" AS "customer_tier", sum("o"."order_total") AS "revenue", (sum("o"."order_total") / nullif(count(*), 0)) AS "aov" FROM {{dataset('ds_orders')}} "o" LEFT JOIN {{dataset('ds_customers')}} "c" ON "o"."customer_id" = "c"."id" GROUP BY 1
-- params: []
