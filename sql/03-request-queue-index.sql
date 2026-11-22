-- 在已应用 02-schema.sql 的数据库中执行一次。
-- 保留按申请类型筛选的 ix_request_queue，补充不限类型的全局待办顺序。
ALTER TABLE request
    ADD INDEX ix_request_queue_time (state, requested_at, request_id);
