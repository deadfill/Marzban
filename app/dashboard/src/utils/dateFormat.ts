import { format } from 'date-fns';

export const dateFormat = (dateString: string) => {
  if (!dateString) return '-';
  try {
    const date = new Date(dateString);
    return format(date, 'yyyy-MM-dd HH:mm:ss');
  } catch (error) {
    return dateString;
  }
}; 