import { useState, useEffect } from 'react';
import API from '../api';

function useFetch<T = any>(endpoint: string, params: Record<string, string> = {}) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchData = async () => {
    try {
      setLoading(true);
      const response = await API.get(endpoint, { params });
      setData(response);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, [endpoint, JSON.stringify(params)]);

  const mutate = () => {
    fetchData();
  };

  return { data, loading, error, mutate };
}

export default useFetch; 