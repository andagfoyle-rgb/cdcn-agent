"""Section 7 — LLM Client offline simulation."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

async def run():
    from app.llm_client import CDCNLLMClient, CDCNLLMError, llm_client

    # 1. Basic instantiation
    client = CDCNLLMClient(base_url='http://127.0.0.1:11434', model='test-model', api_key='')
    assert client.base_url == 'http://127.0.0.1:11434'
    assert client.model == 'test-model'
    print(f'LLM client instantiation: PASSED (model={client.model})')

    # 2. configure() hot-swap
    client.configure('http://192.168.1.1:11434', 'llama3.1:14b')
    assert client.base_url == 'http://192.168.1.1:11434'
    assert client.model == 'llama3.1:14b'
    print('configure() hot-swap: PASSED')

    # 3. Headers with API key
    client2 = CDCNLLMClient(api_key='mykey')
    assert client2._headers == {'Authorization': 'Bearer mykey'}
    client3 = CDCNLLMClient(api_key='')
    assert client3._headers == {}
    print('Auth headers: PASSED')

    # 4. _build_messages with system prompt
    msgs = [{'role': 'user', 'content': 'Hello'}]
    result = client._build_messages(msgs, 'You are a helpful assistant.')
    assert result[0]['role'] == 'system'
    assert len(result) == 2
    print('_build_messages with system prompt: PASSED')

    # 5. Simulate successful chat() call with mock
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        'message': {'content': 'Test response from LLM'},
        'prompt_eval_count': 10,
        'eval_count': 20,
    }
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_context = MagicMock()
    mock_context.__aenter__ = AsyncMock(return_value=mock_client)
    mock_context.__aexit__ = AsyncMock(return_value=False)

    with patch('httpx.AsyncClient', return_value=mock_context):
        response = await client.chat([{'role': 'user', 'content': 'Hello'}])
    assert response == 'Test response from LLM'
    print(f'chat() mock response: PASSED ({response})')

    # 6. Simulate embed() call
    mock_embed_response = MagicMock()
    mock_embed_response.status_code = 200
    mock_embed_response.json.return_value = {'embedding': [0.1] * 768}
    mock_embed_client = MagicMock()
    mock_embed_client.post = AsyncMock(return_value=mock_embed_response)
    mock_embed_ctx = MagicMock()
    mock_embed_ctx.__aenter__ = AsyncMock(return_value=mock_embed_client)
    mock_embed_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch('httpx.AsyncClient', return_value=mock_embed_ctx):
        vector = await client.embed('test text')
    assert len(vector) == 768
    print(f'embed() mock: PASSED (dim={len(vector)})')

    # 7. Connection error raises CDCNLLMError
    with patch('httpx.AsyncClient') as MockHttp:
        instance = MagicMock()
        instance.post = AsyncMock(side_effect=httpx.ConnectError('Connection refused'))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=instance)
        ctx.__aexit__ = AsyncMock(return_value=False)
        MockHttp.return_value = ctx
        try:
            await client.chat([{'role': 'user', 'content': 'test'}])
            assert False, 'Should have raised CDCNLLMError'
        except CDCNLLMError as e:
            print(f'ConnectError raises CDCNLLMError: PASSED ({str(e)[:50]})')

    print('\nSECTION 7 LLM Client: ALL PASSED')

asyncio.run(run())
