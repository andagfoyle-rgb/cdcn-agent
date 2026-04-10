import asyncio, sys
sys.path.insert(0, '/opt/cdcn-agent')
import os; os.chdir('/opt/cdcn-agent')
from app.skills.indexer import DocumentIndexerSkill
async def main():
    s = DocumentIndexerSkill()
    r = await s.run(force=True)
    print(r.output)
asyncio.run(main())
