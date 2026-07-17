REVOKE ALL ON ALL TABLES IN SCHEMA public FROM usage_app;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM usage_app;
REVOKE USAGE ON SCHEMA public FROM usage_app;
-- role kept (may own nothing); dropping is left to the operator.
