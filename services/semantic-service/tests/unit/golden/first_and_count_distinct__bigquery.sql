SELECT `o`.`region` AS `region`, array_agg(`o`.`status` ORDER BY `o`.`order_id` LIMIT 1)[OFFSET(0)] AS `first_status`, count(DISTINCT `o`.`region`) AS `region_count` FROM {{dataset('ds_orders')}} `o` GROUP BY 1
-- params: []
