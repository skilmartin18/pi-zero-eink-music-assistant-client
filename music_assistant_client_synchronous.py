"""Blocking access to the Music Assistant client."""

import asyncio

from music_assistant_client import MusicAssistantClient

# async confuses me, make the client synchronous
class MusicAssistantClientSynchronous:
    def __init__(self, server_url, token):
        self.server_url = server_url
        self.token = token
        self.runner = asyncio.Runner()
        self.client = None

    def _run(self, request):
        async def run_request():
            return await asyncio.wait_for(request, timeout=15)

        return self.runner.run(run_request())

    def __enter__(self):
        async def connect():
            self.client = MusicAssistantClient(self.server_url, None, token=self.token)
            await self.client.connect()

        try:
            self._run(connect())
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if self.client is not None:
                self._run(self.client.disconnect())
        finally:
            self.runner.close()

    def send_command(self, command, **kwargs):
        return self._run(self.client.send_command(command, **kwargs))

    def get_active_queue(self, player_id):
        return self._run(self.client.player_queues.get_active_queue(player_id))

    def get_image_url(self, image):
        return self.client.get_image_url(image)