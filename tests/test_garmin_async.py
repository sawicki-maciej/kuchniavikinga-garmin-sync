"""Test async behavior in GarminClient."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from kuchniavikinga_garmin_sync.garmin_client import GarminConnectClient


@pytest.mark.asyncio
async def test_refresh_session_awaited():
    """Test that refresh_session is properly awaited."""
    client = GarminConnectClient()
    
    # Mock the secret manager client
    with patch('kuchniavikinga_garmin_sync.garmin_client.secretmanager.SecretManagerServiceClient') as mock_sm:
        # Mock the secret response
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = '{"test": "token"}'
        mock_sm.return_value.access_secret_version.return_value = mock_response
        
        # Mock GarminAuth
        with patch('kuchniavikinga_garmin_sync.garmin_client.GarminAuth') as mock_auth_class:
            mock_auth = MagicMock()
            mock_auth.load_session.return_value = True
            
            # Mock refresh_session as async (returns awaitable)
            async def mock_refresh():
                return True
            mock_auth.refresh_session = AsyncMock(side_effect=mock_refresh)
            
            mock_auth.save_session = MagicMock()
            mock_auth_class.return_value = mock_auth
            
            # Mock GarminClient
            with patch('kuchniavikinga_garmin_sync.garmin_client.GarminClient'):
                # Authenticate
                await client.authenticate()
                
                # Verify refresh_session was awaited (called once)
                mock_auth.refresh_session.assert_awaited_once()
                
                # Verify tokens were marked as changed since refresh returned True
                assert client._tokens_changed is True


@pytest.mark.asyncio
async def test_refresh_session_not_needed():
    """Test that tokens are not uploaded when refresh returns False."""
    client = GarminConnectClient()
    
    with patch('kuchniavikinga_garmin_sync.garmin_client.secretmanager.SecretManagerServiceClient') as mock_sm:
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = '{"test": "token"}'
        mock_sm.return_value.access_secret_version.return_value = mock_response
        
        with patch('kuchniavikinga_garmin_sync.garmin_client.GarminAuth') as mock_auth_class:
            mock_auth = MagicMock()
            mock_auth.load_session.return_value = True
            
            # Mock refresh_session returning False (no refresh needed)
            async def mock_no_refresh():
                return False
            mock_auth.refresh_session = AsyncMock(side_effect=mock_no_refresh)
            
            mock_auth_class.return_value = mock_auth
            
            with patch('kuchniavikinga_garmin_sync.garmin_client.GarminClient'):
                await client.authenticate()
                
                # Tokens should NOT be marked as changed
                assert client._tokens_changed is False
