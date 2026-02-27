import asyncio
import asyncpg

async def reset():
    conn = await asyncpg.connect('postgresql://postgres:postgres@db:5432/interview_scheduler')
    await conn.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public;')
    await conn.close()
    print("Schema reset complete.")

if __name__ == "__main__":
    asyncio.run(reset())
