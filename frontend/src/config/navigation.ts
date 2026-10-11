import { House, Library, Search, FlaskConical, Settings } from "lucide-react";
import type { LucideIcon } from "lucide-react";

export type SubmenuItem = {
    to: string
    label: string
    end: boolean
}

export type NavigationGroup = {
    to: string
    label: string
    icon: LucideIcon
    end: boolean,
    children: SubmenuItem[]
}

export const navagationItems: NavigationGroup[] = [
    {
        to: '/overview',
        label: 'Tổng quan',
        icon: House,
        end: false,
        children: [
            {
                to: '/overview/summary',
                label: 'Giới thiệu',
                end: true,
            },
            {
                to: '/overview/process',
                label: 'Quy trình hoạt đông',
                end: true,
            }
        ]
    },
    {
        to: '/library',
        label: 'Kho học liệu',
        icon: Library,
        end: false,
        children: [
            {
                to: '/library/list',
                label: 'Danh sách học liệu',
                end: true,
            },
            {
                to: '/library/add',
                label: 'Thêm học liệu',
                end: true
            },
            {
                to: '/library/processing',
                label: 'Xử lý học liệu',
                end: true
            },
            {
                to: '/library/indexing',
                label: 'Phân đoạn và chỉ mục',
                end: true
            }

        ]
    },
    {
        to: '/search',
        label: 'Tra cứu',
        icon: Search,
        end: false,
        children: [
            {
                to: '/search/materials',
                label: 'Tìm kiếm học liệu',
                end: true
            },
            {
                to: '/search/query-methods',
                label: 'Phương pháp truy vấn',
                end: true,
            },
            {
                to: '/search/result',
                label: 'Kết quả tìm kiếm',
                end: true,
            }
        ]
    },
    {
        to: '/experiments',
        label: 'Thực nghiệm',
        icon: FlaskConical,
        end: false,
        children: [
            {
                to: 'experiments/evaluation-data',
                label: 'Dữ liệu đánh giá',
                end: true,
            },
            {
                to: '/experiments/run',
                label: 'Chạy thực nghiệm',
                end: true,
            },
            {
                to: '/experiments/results',
                label: 'Kết quả đánh giá',
                end: true,
            },
            {
                to: '/experiments/reports',
                label: 'Báo cáo',
                end: true,
            }
        ]
    },
    {
        to: '/systems',
        label: 'Hệ thống',
        icon: Settings,
        end: false,
        children: [
            {
                to: '/systems/status',
                label: 'Trạng thái hệ thống',
                end: true,
            },
            {
                to: '/systems/capabilities',
                label: 'Khả năng xử lý',
                end: true,
            }
        ]
    }
]