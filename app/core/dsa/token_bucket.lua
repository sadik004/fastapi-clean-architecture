-- Token Bucket Algorithm in Redis Lua Script
-- KEYS[1]: client bucket key (e.g. ratelimit:tokenbucket:{scope}:{client_id})
-- ARGV[1]: capacity (number, maximum tokens bucket can hold)
-- ARGV[2]: refill_rate (number, tokens replenished per second)
-- ARGV[3]: requested (number, tokens consumed by this request)
-- ARGV[4]: now (number, current unix timestamp in seconds)

local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local requested = tonumber(ARGV[3])
local now = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'last_updated')
local tokens = tonumber(data[1])
local last_updated = tonumber(data[2])

if not tokens or not last_updated then
    tokens = capacity
    last_updated = now
else
    local delta = math.max(0, now - last_updated)
    tokens = math.min(capacity, tokens + (delta * refill_rate))
    last_updated = now
end

local ttl = 3600
if refill_rate > 0 then
    ttl = math.ceil(capacity / refill_rate) + 2
end

if tokens >= requested then
    local new_tokens = tokens - requested
    redis.call('HMSET', key, 'tokens', tostring(new_tokens), 'last_updated', tostring(last_updated))
    redis.call('EXPIRE', key, ttl)
    return {1, tostring(new_tokens), "0"}
else
    local retry_after = 3600
    if refill_rate > 0 then
        retry_after = (requested - tokens) / refill_rate
    end
    redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_updated', tostring(last_updated))
    redis.call('EXPIRE', key, ttl)
    return {0, tostring(tokens), tostring(retry_after)}
end
