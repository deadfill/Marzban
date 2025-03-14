import { useEffect, useState } from "react";
import { Button, Card, Col, Divider, Form, Input, Modal, Popconfirm, Row, Select, Space, Table, Tag, message as antMessage, Tabs } from "antd";
import { DeleteOutlined, PauseCircleOutlined, PlayCircleOutlined, SendOutlined, EyeOutlined, ClockCircleOutlined } from "@ant-design/icons";
import API from "../api";
import { useTranslation } from "react-i18next";
import { dateFormat } from "../utils/dateFormat";
import { getAuthToken } from "../utils/authStorage";


interface MessageTask {
  id: number;
  task_type: string;
  cron_expression: string;
  message_text: string;
  is_active: boolean;
  created_at: string;
  last_run: string | null;
  next_run: string | null;
}

// Компонент для отображения предпросмотра Telegram сообщения
const TelegramPreview = ({ html, imageUrl, t }: { html: string, imageUrl?: string, t: any }) => {
  return (
    <div style={{ 
      marginBottom: 24,
      border: '1px solid #ddd',
      borderRadius: 8,
      backgroundColor: '#f9f9f9',
      padding: 16,
      maxWidth: '100%',
      boxShadow: '0 2px 8px rgba(0, 0, 0, 0.1)'
    }}>
      <div style={{ 
        marginBottom: imageUrl ? 12 : 0,
        fontWeight: 'bold',
        color: '#333'
      }}>
        {t("messages.preview")}:
      </div>
      
      {imageUrl && (
        <div style={{ marginBottom: 12 }}>
          <img 
            src={imageUrl} 
            alt="Preview" 
            style={{ 
              maxWidth: '100%', 
              maxHeight: 300, 
              borderRadius: 4,
              marginBottom: 8
            }} 
          />
        </div>
      )}
      
      <div 
        style={{ 
          backgroundColor: 'white',
          padding: 12,
          borderRadius: 8,
          border: '1px solid #e1e1e1',
          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
          fontSize: 14,
          lineHeight: 1.4,
          color: '#222'
        }}
        dangerouslySetInnerHTML={{ __html: html }} 
      />
    </div>
  );
};

// Новый компонент для отображения системных CRON-задач
const SystemCronJobs = () => {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);
  const [cronJobs, setCronJobs] = useState<any[]>([]);

  const fetchSystemCronJobs = async () => {
    setLoading(true);
    try {
      const { data } = await API.get("/api/messages/system-cron-jobs");
      setCronJobs(data.jobs);
    } catch (error) {
      console.error("Ошибка при получении системных CRON-задач:", error);
      antMessage.error(t("messages.fetchError"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSystemCronJobs();
  }, []);

  const columns = [
    {
      title: t("messages.id"),
      dataIndex: "id",
      key: "id",
    },
    {
      title: t("messages.name"),
      dataIndex: "name",
      key: "name",
      render: (text: string) => text || "-",
    },
    {
      title: t("messages.function"),
      dataIndex: "func",
      key: "func",
      ellipsis: true,
    },
    {
      title: t("messages.trigger"),
      dataIndex: "trigger",
      key: "trigger",
      ellipsis: true,
    },
    {
      title: t("messages.nextRunTime"),
      dataIndex: "next_run_time",
      key: "next_run_time",
      render: (text: string) => text ? dateFormat(text) : t("messages.paused"),
    },
    {
      title: t("messages.misfireGraceTime"),
      dataIndex: "misfire_grace_time",
      key: "misfire_grace_time",
      render: (value: number) => (value ? `${value} ${t("messages.seconds")}` : "-"),
    },
    {
      title: t("messages.maxInstances"),
      dataIndex: "max_instances",
      key: "max_instances",
      render: (value: number) => value || "-",
    },
    {
      title: t("messages.interval"),
      dataIndex: "interval_seconds",
      key: "interval_seconds",
      render: (value: number) => {
        if (!value) return "-";
        if (value < 60) return `${value} ${t("messages.seconds")}`;
        if (value < 3600) return `${Math.floor(value / 60)} ${t("messages.minutes")}`;
        return `${Math.floor(value / 3600)} ${t("messages.hours")}`;
      },
    },
  ];

  return (
    <Card title={t("messages.systemCronJobs")}>
      <div style={{ marginBottom: 16 }}>
        <Button 
          type="primary" 
          icon={<ClockCircleOutlined />}
          onClick={fetchSystemCronJobs}
          loading={loading}
        >
          {t("messages.refreshJobs")}
        </Button>
      </div>
      <Table
        loading={loading}
        dataSource={cronJobs}
        columns={columns}
        rowKey="id"
        scroll={{ x: 'max-content' }}
        expandable={{
          expandedRowRender: (record) => (
            <div>
              {record.cron_fields && (
                <div style={{ marginBottom: 16 }}>
                  <strong>{t("messages.cronExpression")}:</strong>
                  <ul>
                    {record.cron_fields.map((field: string, index: number) => (
                      <li key={index}>{field}</li>
                    ))}
                  </ul>
                </div>
              )}
              {record.args && (
                <div style={{ marginBottom: 8 }}>
                  <strong>{t("messages.arguments")}:</strong> {record.args}
                </div>
              )}
              {record.kwargs && (
                <div style={{ marginBottom: 8 }}>
                  <strong>{t("messages.kwargs")}:</strong> {record.kwargs}
                </div>
              )}
            </div>
          ),
        }}
      />
    </Card>
  );
};

const Messages = () => {
  const { t } = useTranslation();
  const [form] = Form.useForm();
  const [sendForm] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [isModalVisible, setIsModalVisible] = useState(false);
  const [isSendModalVisible, setIsSendModalVisible] = useState(false);
  const [sendToAll, setSendToAll] = useState(true);
  const [tasks, setTasks] = useState<MessageTask[]>([]);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  // Новое состояние для предпросмотра
  const [previewMessage, setPreviewMessage] = useState<string>("");
  const [previewImageUrl, setPreviewImageUrl] = useState<string | undefined>(undefined);
  
  // Функция для загрузки задач
  const fetchTasks = async () => {
    try {
      setTasksLoading(true);
      const data = await API.get('/messages/tasks');
      setTasks(data);
    } catch (error) {
      console.error('Error fetching tasks:', error);
    } finally {
      setTasksLoading(false);
    }
  };
  
  // Загружаем задачи при монтировании компонента
  useEffect(() => {
    fetchTasks();
  }, []);
  
  // Set initial cron for selected task type
  const updateCronExpression = (value: string) => {
    switch (value) {
      case "expiration_7days":
      case "expiration_3days":
      case "expiration_1day":
        form.setFieldsValue({ cron_expression: "0 12 * * *" });
        break;
      default:
        form.setFieldsValue({ cron_expression: "" });
    }
  };

  // Handle creating a new task
  const handleCreateTask = async (values: any) => {
    setLoading(true);
    try {
      await API.post('/messages/tasks', values);
      antMessage.success(t("messages.taskCreated"));
      form.resetFields();
      setIsModalVisible(false);
      fetchTasks(); // Обновляем список задач
    } catch (error) {
      console.error("Error creating task:", error);
      antMessage.error(t("messages.errorCreatingTask"));
    } finally {
      setLoading(false);
    }
  };

  // Toggle task active status
  const toggleTaskStatus = async (id: number) => {
    try {
      await API.post(`/messages/tasks/${id}/toggle`);
      fetchTasks(); // Обновляем список задач
      antMessage.success(t("messages.taskToggled"));
    } catch (error) {
      console.error("Error toggling task:", error);
      antMessage.error(t("messages.errorTogglingTask"));
    }
  };

  // Delete a task
  const deleteTask = async (id: number) => {
    try {
      await API.delete(`/messages/tasks/${id}`);
      fetchTasks(); // Обновляем список задач
      antMessage.success(t("messages.taskDeleted"));
    } catch (error) {
      console.error("Error deleting task:", error);
      antMessage.error(t("messages.errorDeletingTask"));
    }
  };

  // Обработчик изменения текста сообщения для предпросмотра
  const handleMessageChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setPreviewMessage(e.target.value || "");
  };

  // Обработчик изменения изображения для предпросмотра
  const handleImageChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0];
    setFile(selectedFile || null);
    
    if (selectedFile) {
      const imageUrl = URL.createObjectURL(selectedFile);
      setPreviewImageUrl(imageUrl);
    } else {
      setPreviewImageUrl(undefined);
    }
  };

  // Send a message
  const handleSendMessage = async (values: any) => {
    setLoading(true);
    try {
        const formData = new FormData();
        formData.append("message", values.message);
        formData.append("all_users", "true"); // Всегда всем пользователям

        if (file) {
            formData.append("image", file);
        }

        // Вместо прямого вызова fetch используем дополнительные параметры запроса
        // для сохранения токена авторизации через API клиент
        const response = await fetch('/api/messages/send', {
            method: 'POST',
            headers: {
              'Authorization': `Bearer ${getAuthToken()}`
            },
            body: formData
        });

        if (response.ok) {
            const data = await response.json();
            antMessage.success(data.message || "Сообщение отправлено!");
            sendForm.resetFields();
            setFile(null);
            setPreviewImageUrl(undefined);
            setPreviewMessage("");
            setIsSendModalVisible(false);
        } else {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Ошибка при отправке');
        }
    } catch (error) {
        console.error("Ошибка отправки:", error);
        antMessage.error("Ошибка при отправке сообщения");
    } finally {
        setLoading(false);
    }
  };

  // Table columns
  const columns = [
    {
      title: t("messages.taskType"),
      dataIndex: "task_type",
      key: "task_type",
      render: (text: string) => {
        let color = "blue";
        let label = text;

        if (text.startsWith("expiration_")) {
          color = "orange";
          const days = text.split("_")[1];
          label = t("messages.expirationNotification", { days });
        }

        return <Tag color={color}>{label}</Tag>;
      }
    },
    {
      title: t("messages.cronExpression"),
      dataIndex: "cron_expression",
      key: "cron_expression"
    },
    {
      title: t("messages.message"),
      dataIndex: "message_text",
      key: "message_text",
      render: (text: string) => text.length > 50 ? `${text.substring(0, 50)}...` : text
    },
    {
      title: t("messages.status"),
      dataIndex: "is_active",
      key: "is_active",
      render: (active: boolean) => (
        <Tag color={active ? "green" : "red"}>
          {active ? t("messages.active") : t("messages.inactive")}
        </Tag>
      )
    },
    {
      title: t("messages.lastRun"),
      dataIndex: "last_run",
      key: "last_run",
      render: (text: string) => text ? dateFormat(text) : "-"
    },
    {
      title: t("messages.nextRun"),
      dataIndex: "next_run",
      key: "next_run",
      render: (text: string) => text ? dateFormat(text) : "-"
    },
    {
      title: t("messages.actions"),
      key: "actions",
      render: (_: unknown, record: MessageTask) => (
        <Space size="small">
          <Button 
            type="text"
            icon={record.is_active ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
            onClick={() => toggleTaskStatus(record.id)}
          />
          <Popconfirm
            title={t("messages.confirmDelete")}
            onConfirm={() => deleteTask(record.id)}
            okText={t("yes")}
            cancelText={t("no")}
          >
            <Button type="text" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      )
    }
  ];

  return (
    <div>
      <Tabs defaultActiveKey="tasks" items={[
        {
          key: 'tasks',
          label: t("messages.messageTasks"),
          children: (
            <>
              <Card title={t("messages.sendMessage")}>
                {/* Форма отправки сообщения */}
                <Form onFinish={handleSendMessage} layout="vertical">
                  <Row gutter={16}>
                    <Col xs={24} md={12}>
                      <Form.Item 
                        name="message" 
                        label={t("messages.message")}
                        rules={[{ required: true, message: t("messages.messageRequired") }]}
                      >
                        <Input.TextArea
                          rows={4}
                          onChange={handleMessageChange}
                          placeholder={t("messages.messagePlaceholder")}
                        />
                      </Form.Item>
                    </Col>
                    <Col xs={24} md={12}>
                      <TelegramPreview html={previewMessage} imageUrl={previewImageUrl} t={t} />
                    </Col>
                  </Row>
                  <Form.Item>
                    <Space>
                      <Button type="primary" htmlType="submit" icon={<SendOutlined />} loading={loading}>
                        {t("messages.sendToAll")}
                      </Button>
                    </Space>
                  </Form.Item>
                </Form>
              </Card>

              <Divider />

              <Card title={t("messages.scheduledMessages")}>
                <div style={{ marginBottom: 16 }}>
                  <Button type="primary" onClick={() => setIsModalVisible(true)}>
                    {t("messages.createTask")}
                  </Button>
                </div>
                <Table
                  loading={tasksLoading}
                  dataSource={tasks}
                  rowKey="id"
                  pagination={false}
                  columns={[
                    {
                      title: t("messages.type"),
                      dataIndex: "task_type",
                      key: "task_type",
                      render: (text) => {
                        let color = "blue";
                        if (text.startsWith("expiration_")) {
                          color = "orange";
                        }
                        return <Tag color={color}>{text}</Tag>;
                      },
                    },
                    {
                      title: t("messages.cronExpression"),
                      dataIndex: "cron_expression",
                      key: "cron_expression",
                    },
                    {
                      title: t("messages.messagePreview"),
                      dataIndex: "message_text",
                      key: "message_text",
                      ellipsis: true,
                      render: (text) => (
                        <div style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {text}
                        </div>
                      ),
                    },
                    {
                      title: t("messages.lastRun"),
                      dataIndex: "last_run",
                      key: "last_run",
                      render: (text) => (text ? dateFormat(text) : "-"),
                    },
                    {
                      title: t("messages.nextRun"),
                      dataIndex: "next_run",
                      key: "next_run",
                      render: (text) => (text ? dateFormat(text) : "-"),
                    },
                    {
                      title: t("messages.status"),
                      dataIndex: "is_active",
                      key: "is_active",
                      render: (active) => (
                        <Tag color={active ? "success" : "error"}>
                          {active ? t("messages.active") : t("messages.paused")}
                        </Tag>
                      ),
                    },
                    {
                      title: t("messages.actions"),
                      key: "actions",
                      render: (_, record) => (
                        <Space>
                          <Button
                            type="text"
                            icon={record.is_active ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
                            onClick={() => toggleTaskStatus(record.id)}
                          />
                          <Button 
                            type="text" 
                            icon={<EyeOutlined />} 
                            onClick={() => {
                              setPreviewMessage(record.message_text);
                              setPreviewImageUrl(undefined);
                            }}
                          />
                          <Popconfirm
                            title={t("messages.deleteConfirm")}
                            onConfirm={() => deleteTask(record.id)}
                            okText={t("common.yes")}
                            cancelText={t("common.no")}
                          >
                            <Button type="text" danger icon={<DeleteOutlined />} />
                          </Popconfirm>
                        </Space>
                      ),
                    },
                  ]}
                />
              </Card>

              {/* Модальное окно создания задачи */}
              <Modal
                title={t("messages.createTask")}
                open={isModalVisible}
                onCancel={() => setIsModalVisible(false)}
                footer={null}
              >
                <Form onFinish={handleCreateTask} layout="vertical">
                  <Form.Item
                    name="task_type"
                    label={t("messages.taskType")}
                    rules={[{ required: true, message: t("messages.taskTypeRequired") }]}
                  >
                    <Select placeholder={t("messages.selectTaskType")}>
                      <Select.Option value="broadcast">
                        {t("messages.broadcastMessage")}
                      </Select.Option>
                      <Select.Option value="expiration_7days">
                        {t("messages.expirationWarning7days")}
                      </Select.Option>
                      <Select.Option value="expiration_3days">
                        {t("messages.expirationWarning3days")}
                      </Select.Option>
                      <Select.Option value="expiration_1day">
                        {t("messages.expirationWarning1day")}
                      </Select.Option>
                    </Select>
                  </Form.Item>
                  <Form.Item
                    name="cron_expression"
                    label={t("messages.cronExpression")}
                    rules={[{ required: true, message: t("messages.cronRequired") }]}
                    extra={t("messages.cronHelp")}
                  >
                    <Select
                      placeholder={t("messages.selectOrCustom")}
                      allowClear
                      onChange={updateCronExpression}
                      options={[
                        {
                          label: t("messages.commonSchedules"),
                          options: [
                            { label: t("messages.everyMinute"), value: "* * * * *" },
                            { label: t("messages.everyHour"), value: "0 * * * *" },
                            { label: t("messages.everyDay8am"), value: "0 8 * * *" },
                            { label: t("messages.everyMonday8am"), value: "0 8 * * 1" },
                            { label: t("messages.every1st8am"), value: "0 8 1 * *" },
                          ],
                        },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item
                    name="message_text"
                    label={t("messages.messageText")}
                    rules={[{ required: true, message: t("messages.messageRequired") }]}
                    extra={t("messages.messageHelp")}
                  >
                    <Input.TextArea
                      rows={4}
                      placeholder={t("messages.messagePlaceholder")}
                    />
                  </Form.Item>
                  <Form.Item>
                    <TelegramPreview html={previewMessage} t={t} />
                  </Form.Item>
                  <Form.Item>
                    <Button type="primary" htmlType="submit" loading={loading}>
                      {t("messages.createTask")}
                    </Button>
                  </Form.Item>
                </Form>
              </Modal>
            </>
          ),
        },
        {
          key: 'systemCron',
          label: t("messages.systemCronJobs"),
          children: <SystemCronJobs />
        }
      ]} />
    </div>
  );
};

export default Messages; 