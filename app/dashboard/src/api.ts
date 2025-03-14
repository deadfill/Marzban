import { getAuthToken } from "./utils/authStorage";

const API_BASE_URL = "/api";

interface FetchOptions extends RequestInit {
  params?: Record<string, string>;
}

class API {
  static async request(endpoint: string, options: FetchOptions = {}) {
    const { params, ...fetchOptions } = options;
    
    // Добавляем параметры запроса к URL
    const url = new URL(`${API_BASE_URL}${endpoint}`, window.location.origin);
    if (params) {
      Object.entries(params).forEach(([key, value]) => {
        url.searchParams.append(key, value);
      });
    }
    
    // Добавляем токен авторизации для всех запросов
    const headers = new Headers(fetchOptions.headers);
    const token = getAuthToken();
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
    
    const response = await fetch(url.toString(), {
      ...fetchOptions,
      headers,
    });
    
    if (!response.ok) {
      throw new Error(`API error: ${response.status}`);
    }
    
    return response.json();
  }
  
  static async get(endpoint: string, options: FetchOptions = {}) {
    return this.request(endpoint, { ...options, method: "GET" });
  }
  
  static async post(endpoint: string, data?: any, options: FetchOptions = {}) {
    const headers = new Headers(options.headers);
    headers.set("Content-Type", "application/json");
    
    return this.request(endpoint, {
      ...options,
      method: "POST",
      headers,
      body: JSON.stringify(data),
    });
  }
  
  static async put(endpoint: string, data?: any, options: FetchOptions = {}) {
    const headers = new Headers(options.headers);
    headers.set("Content-Type", "application/json");
    
    return this.request(endpoint, {
      ...options,
      method: "PUT",
      headers,
      body: JSON.stringify(data),
    });
  }
  
  static async delete(endpoint: string, options: FetchOptions = {}) {
    return this.request(endpoint, { ...options, method: "DELETE" });
  }
}

export default API; 