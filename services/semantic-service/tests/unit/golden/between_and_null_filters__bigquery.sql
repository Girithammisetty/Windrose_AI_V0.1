SELECT `o`.`region` AS `region`, sum(`o`.`order_total`) AS `revenue` FROM {{dataset('ds_orders')}} `o` WHERE `o`.`order_date` BETWEEN $1 AND $2 AND `o`.`status` IS NOT NULL GROUP BY 1
-- params: [{"type": "date", "value": "2026-01-01"}, {"type": "date", "value": "2026-06-30"}]
