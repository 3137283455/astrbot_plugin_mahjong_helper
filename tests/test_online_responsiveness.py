import asyncio
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch
from astrbot_plugin_group_data.online import OnlineSync
from astrbot_plugin_group_data.core import validate
import test_online
from test_periods import normal


class ProjectionResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture=test_online.OnlineTests();self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.online=self.fixture.online;self.store=self.fixture.store
        self.source=self.fixture.seed('2','new')

    async def test_slow_projection_leaves_the_message_loop_responsive(self):
        original=OnlineSync.projection;threads=[];main=threading.get_ident()
        def slow(reader):
            threads.append(threading.get_ident());time.sleep(.18)
            return original(reader)
        with patch.object(OnlineSync,'projection',slow):
            started=time.monotonic();task=asyncio.create_task(self.online.projected())
            try:
                await asyncio.sleep(.025)
                self.assertLess(time.monotonic()-started,.12)
                self.assertFalse(task.done())
                self.assertTrue(self.store.group(self.fixture.scope)['enabled'])
            finally:await task
        self.assertTrue(threads);self.assertTrue(all(t!=main for t in threads))

    async def test_many_rows_share_one_snapshot_until_real_data_changes(self):
        for i in range(3,23):
            row=self.fixture.seed(str(i),'hash'+str(i))
            self.store.save(row['hash'],'accepted',validate({**normal(),'game_id':'perf-game-'+str(i)}))
        original=self.online._read_projection
        with patch.object(self.online,'_read_projection',wraps=original) as compute:
            await self.online.once()
            self.assertLessEqual(compute.call_count,2)
            before=compute.call_count
            await self.online.once()
            self.assertEqual(compute.call_count-before,1)

    async def test_edit_during_snapshot_discards_old_result(self):
        entered=threading.Event();release=threading.Event();calls=[]
        original=OnlineSync.projection
        def delayed(reader):
            result=original(reader);calls.append(1)
            if len(calls)==1:
                entered.set();release.wait(2)
            return result
        with patch.object(OnlineSync,'projection',delayed):
            task=asyncio.create_task(self.online.projected())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,1))
                self.store.override(self.fixture.scope,self.source,'accepted',normal(['已修正','乙','丙','丁']))
            finally:release.set()
            desired=(await task)[0]
        self.assertTrue(any(row.get('一位昵称')=='已修正' for row in desired.values()))
        self.assertEqual(len(calls),2)

    async def test_worker_connection_cannot_write_to_the_store(self):
        original=OnlineSync.projection;denied=[]
        def probe(reader):
            try:reader.store.db.execute('UPDATE groups SET enabled=0')
            except sqlite3.OperationalError:denied.append(True)
            return original(reader)
        with patch.object(OnlineSync,'projection',probe):await self.online.projected()
        self.assertEqual(denied,[True])
        self.assertTrue(self.store.group(self.fixture.scope)['enabled'])


if __name__=='__main__':unittest.main()
