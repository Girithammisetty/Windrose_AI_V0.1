SELECT sum([o].[order_total]) AS [revenue], count(*) AS [order_count] FROM {{dataset('ds_orders')}} [o]
-- params: []
